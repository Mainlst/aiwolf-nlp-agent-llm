package service

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"github.com/aiwolfdial/aiwolf-nlp-server/model"
	"github.com/aiwolfdial/aiwolf-nlp-server/util"
	"github.com/grafov/m3u8"
)

const (
	playlistFile = "playlist.m3u8"
)

type TTSBroadcaster struct {
	config  model.TTSBroadcasterConfig
	baseURL *url.URL
	client  *http.Client
	streams sync.Map
}

type Stream struct {
	isStreaming     int32
	lastSegmentTime int64
	segmentCounter  int64
	playlist        *m3u8.MediaPlaylist
	playlistMu      sync.Mutex
}

func NewTTSBroadcaster(config model.Config) *TTSBroadcaster {
	baseURL, err := url.Parse(config.TTSBroadcaster.Host)
	if err != nil {
		slog.Error("音声合成サーバのURLの解析に失敗しました", "error", err)
		baseURL = &url.URL{
			Scheme: "http",
			Host:   "localhost:50021",
		}
	}

	return &TTSBroadcaster{
		config:  config.TTSBroadcaster,
		baseURL: baseURL,
		client: &http.Client{
			Timeout: config.TTSBroadcaster.Timeout,
		},
	}
}

func (t *TTSBroadcaster) Start() {
	if err := os.MkdirAll(t.config.SegmentDir, 0755); err != nil {
		slog.Error("セグメントディレクトリの作成に失敗しました", "error", err)
		return
	}
	t.cleanupSegments()
}

func (t *TTSBroadcaster) getStream(id string) *Stream {
	if streamInterface, exists := t.streams.Load(id); exists {
		return streamInterface.(*Stream)
	}
	return nil
}

func (t *TTSBroadcaster) CreateStream(id string) {
	if _, exists := t.streams.Load(id); exists {
		return
	}

	stream := &Stream{
		isStreaming:     0,
		lastSegmentTime: time.Now().UnixNano(),
		segmentCounter:  0,
	}

	streamDir := t.getSegmentDir(id)
	if err := os.MkdirAll(streamDir, 0755); err != nil {
		slog.Error("ストリームディレクトリの作成に失敗しました", "error", err, "id", id)
		return
	}

	playlist, err := m3u8.NewMediaPlaylist(math.MaxInt16, math.MaxInt16)
	if err != nil {
		slog.Error("プレイリストの作成に失敗しました", "error", err, "id", id)
		return
	}

	playlist.TargetDuration = float64(t.config.TargetDuration.Seconds())
	playlist.SetVersion(3)
	playlist.Closed = false
	stream.playlist = playlist

	if _, loaded := t.streams.LoadOrStore(id, stream); loaded {
		return
	}

	t.writePlaylist(id, stream)
	slog.Info("ストリームを作成しました", "id", id)
}

func (t *TTSBroadcaster) cleanupSegments() {
	if err := os.RemoveAll(t.config.SegmentDir); err != nil {
		slog.Error("セグメントディレクトリの削除に失敗しました", "error", err)
		return
	}
	slog.Info("セグメントディレクトリのクリーンアップが完了しました")
	if err := os.MkdirAll(t.config.SegmentDir, 0755); err != nil {
		slog.Error("セグメントディレクトリの作成に失敗しました", "error", err)
		return
	}
}

func (t *TTSBroadcaster) getSegmentDir(id string) string {
	cleanID := filepath.Base(filepath.Clean(id))
	return filepath.Join(t.config.SegmentDir, cleanID)
}

func (t *TTSBroadcaster) writePlaylist(id string, stream *Stream) {
	streamDir := t.getSegmentDir(id)
	playlistPath := filepath.Join(streamDir, playlistFile)

	if err := os.MkdirAll(streamDir, 0755); err != nil {
		slog.Error("プレイリストディレクトリの作成に失敗しました", "error", err, "id", id)
		return
	}

	if err := os.WriteFile(playlistPath, stream.playlist.Encode().Bytes(), 0644); err != nil {
		slog.Error("プレイリストの書き込みに失敗しました", "error", err, "id", id)
	}
}

func (t *TTSBroadcaster) BroadcastText(id string, text string, speaker int) {
	if text == model.T_SKIP || text == model.T_OVER {
		return
	}

	if t.config.Async {
		t.broadcastTextAsync(id, text, speaker)
	} else {
		t.broadcastText(id, text, speaker)
	}
}

func (t *TTSBroadcaster) broadcastTextAsync(id string, text string, speaker int) {
	stream := t.getStream(id)
	if stream == nil {
		return
	}

	atomic.StoreInt32(&stream.isStreaming, 1)
	go func() {
		defer func() {
			atomic.StoreInt32(&stream.isStreaming, 0)
			atomic.StoreInt64(&stream.lastSegmentTime, time.Now().UnixNano())
		}()

		ctx, cancel := context.WithTimeout(context.Background(), t.config.Timeout)
		defer cancel()

		audioQuery, err := t.fetchAudioQuery(ctx, text, speaker)
		if err != nil {
			slog.Error("オーディオクエリの取得に失敗しました", "error", err, "id", id)
			return
		}

		if _, err := t.synthesizeAndProcessAudio(ctx, audioQuery, id, stream, speaker); err != nil {
			slog.Error("音声合成に失敗しました", "error", err, "id", id)
		}
	}()
}

func (t *TTSBroadcaster) broadcastText(id string, text string, speaker int) {
	stream := t.getStream(id)
	if stream == nil {
		return
	}

	atomic.StoreInt32(&stream.isStreaming, 1)
	defer func() {
		atomic.StoreInt32(&stream.isStreaming, 0)
		atomic.StoreInt64(&stream.lastSegmentTime, time.Now().UnixNano())
	}()

	ctx, cancel := context.WithTimeout(context.Background(), t.config.Timeout)
	defer cancel()

	audioQuery, err := t.fetchAudioQuery(ctx, text, speaker)
	if err != nil {
		slog.Error("オーディオクエリの取得に失敗しました", "error", err, "id", id)
		return
	}

	duration, err := t.synthesizeAndProcessAudio(ctx, audioQuery, id, stream, speaker)
	if err != nil {
		slog.Error("音声合成に失敗しました", "error", err, "id", id)
		return
	}

	time.Sleep(time.Duration(duration * float64(time.Second)))
}

func (t *TTSBroadcaster) fetchAudioQuery(ctx context.Context, text string, speaker int) ([]byte, error) {
	baseURL := *t.baseURL
	baseURL.Path = "/audio_query"
	params := url.Values{}
	params.Add("speaker", fmt.Sprintf("%d", speaker))
	params.Add("text", text)
	baseURL.RawQuery = params.Encode()
	queryURL := baseURL.String()

	req, err := http.NewRequestWithContext(ctx, "POST", queryURL, nil)
	if err != nil {
		return nil, fmt.Errorf("オーディオクエリリクエスト作成に失敗しました: %w", err)
	}

	resp, err := t.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("オーディオクエリ送信に失敗しました: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("オーディオクエリエラー: ステータスコード %d", resp.StatusCode)
	}

	queryParams, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("オーディオクエリ読み取りに失敗しました: %w", err)
	}

	return queryParams, nil
}

func (t *TTSBroadcaster) synthesizeAndProcessAudio(ctx context.Context, queryParams []byte, id string, stream *Stream, speaker int) (float64, error) {
	baseURL := *t.baseURL
	baseURL.Path = "/synthesis"
	params := url.Values{}
	params.Add("speaker", fmt.Sprintf("%d", speaker))
	baseURL.RawQuery = params.Encode()
	queryURL := baseURL.String()

	req, err := http.NewRequestWithContext(ctx, "POST", queryURL, bytes.NewBuffer(queryParams))
	if err != nil {
		return 0, fmt.Errorf("合成リクエスト作成に失敗しました: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := t.client.Do(req)
	if err != nil {
		return 0, fmt.Errorf("合成リクエスト送信に失敗しました: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("合成エラー: ステータスコード %d", resp.StatusCode)
	}

	wavData, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, fmt.Errorf("合成データ読み取りに失敗しました: %w", err)
	}

	counter := atomic.AddInt64(&stream.segmentCounter, 1)
	baseName := fmt.Sprintf("segment_%d", counter-1)

	segmentParams := util.ConvertWavToSegmentParams{
		FfmpegPath:      t.config.FfmpegPath,
		FfprobePath:     t.config.FfprobePath,
		DurationArgs:    t.config.DurationArgs,
		ConvertArgs:     t.config.ConvertArgs,
		PreConvertArgs:  t.config.PreConvertArgs,
		SplitArgs:       t.config.SplitArgs,
		TempDir:         t.config.TempDir,
		SegmentDuration: t.config.TargetDuration.Seconds(),
		Data:            wavData,
		BaseDir:         t.getSegmentDir(id),
		BaseName:        baseName,
	}

	segmentNames, err := util.ConvertWavToSegment(segmentParams)
	if err != nil {
		return 0, fmt.Errorf("WAVからセグメントへの変換に失敗しました: %w", err)
	}

	return t.addSegmentsToPlaylist(id, stream, segmentNames), nil
}

func (t *TTSBroadcaster) addSegmentsToPlaylist(id string, stream *Stream, segmentNames []string) float64 {
	if len(segmentNames) == 0 {
		return 0
	}

	streamDir := t.getSegmentDir(id)

	stream.playlistMu.Lock()
	defer stream.playlistMu.Unlock()

	var totalDuration float64
	for _, segmentName := range segmentNames {
		segmentPath := filepath.Join(streamDir, segmentName)
		duration, err := util.GetDuration(t.config.FfprobePath, t.config.DurationArgs, segmentPath)
		if err != nil {
			slog.Error("セグメント再生時間の取得に失敗しました", "error", err, "id", id, "segment", segmentName)
			duration = t.config.TargetDuration.Seconds()
		}

		totalDuration += duration

		if err := stream.playlist.AppendSegment(&m3u8.MediaSegment{
			URI:      segmentName,
			Duration: duration,
		}); err != nil {
			slog.Error("プレイリストへのセグメント追加に失敗しました", "error", err, "id", id, "segment", segmentName)
		}
	}

	t.writePlaylist(id, stream)
	return totalDuration
}
