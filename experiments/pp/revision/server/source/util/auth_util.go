package util

import (
	"errors"
	"log/slog"

	"github.com/golang-jwt/jwt/v5"
)

func IsValidPlayerToken(secret string, tokenString string, team string) bool {
	slog.Info("参加者トークンを検証します", "token", tokenString, "team", team)
	token, err := jwt.Parse(tokenString, func(token *jwt.Token) (any, error) {
		if _, exists := token.Method.(*jwt.SigningMethodHMAC); !exists {
			return nil, errors.New("unexpected signing method")
		}
		return []byte(secret), nil
	})
	if err != nil {
		slog.Warn("トークンの検証に失敗しました", "error", err)
		return false
	}
	if !token.Valid {
		slog.Warn("トークンの有効期限が切れています")
		return false
	}
	if claims, exists := token.Claims.(jwt.MapClaims); exists {
		if claims["team"] == team && claims["role"] == "PLAYER" {
			slog.Info("トークンが有効です")
			return true
		}
	} else {
		slog.Warn("クレームの取得に失敗しました")
	}
	return false
}

func IsValidReceiver(secret string, tokenString string) bool {
	slog.Info("閲覧者トークンを検証します", "token", tokenString)
	token, err := jwt.Parse(tokenString, func(token *jwt.Token) (any, error) {
		if _, exists := token.Method.(*jwt.SigningMethodHMAC); !exists {
			return nil, errors.New("unexpected signing method")
		}
		return []byte(secret), nil
	})
	if err != nil {
		slog.Warn("トークンの検証に失敗しました", "error", err)
		return false
	}
	if !token.Valid {
		slog.Warn("トークンの有効期限が切れています")
		return false
	}
	if claims, exists := token.Claims.(jwt.MapClaims); exists {
		if claims["role"] == "RECEIVER" {
			slog.Info("トークンが有効です")
			return true
		}
	} else {
		slog.Warn("クレームの取得に失敗しました")
	}
	return false
}
