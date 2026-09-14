package model

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"github.com/gorilla/websocket"
)

type Connection struct {
	TeamName     string
	OriginalName string
	Conn         *websocket.Conn
	Header       *http.Header
}

func NewConnection(conn *websocket.Conn, header *http.Header) (*Connection, error) {
	req, err := json.Marshal(Packet{
		Request: &R_NAME,
	})
	if err != nil {
		slog.Error("NAMEパケットの作成に失敗しました", "error", err)
		return nil, err
	}
	err = conn.WriteMessage(websocket.TextMessage, req)
	if err != nil {
		slog.Error("NAMEパケットの送信に失敗しました", "error", err)
		return nil, err
	}
	slog.Info("NAMEパケットを送信しました", "remote_addr", conn.RemoteAddr().String())
	_, res, err := conn.ReadMessage()
	if err != nil {
		slog.Error("NAMEリクエストの受信に失敗しました", "error", err)
		return nil, err
	}
	originalName := strings.TrimRight(string(res), "\n")
	teamName := strings.TrimRight(originalName, "1234567890")
	connection := Connection{
		TeamName:     teamName,
		OriginalName: originalName,
		Conn:         conn,
		Header:       header,
	}
	slog.Info("クライアントが接続しました", "team_name", connection.TeamName, "original_name", connection.OriginalName, "remote_addr", conn.RemoteAddr().String())
	return &connection, nil
}
