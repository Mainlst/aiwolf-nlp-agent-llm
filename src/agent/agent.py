"""Module that defines the base class for agents.

エージェントの基底クラスを定義するモジュール.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

from dotenv import load_dotenv
from jinja2 import Template
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from aiwolf_nlp_common.packet import Info, Packet, Request, Role, Setting, Status, Talk

from utils.agent_logger import AgentLogger
from utils.stoppable_thread import StoppableThread
from utils import request_audit

if TYPE_CHECKING:
    from collections.abc import Callable

P = ParamSpec("P")
T = TypeVar("T")

# Fallback freeform length limits, used only when the server omits max_length.
# Aligned with the 125-character per-utterance cap enforced by the output guard
# so English utterances are not over-truncated when no server setting is present.
BASE_FREEFORM_TEXT_LENGTH = 125
MENTION_FREEFORM_TEXT_LENGTH = 125
MIN_SENTENCE_TRUNCATE_RATIO = 0.8
MAX_ENDGAME_ALIVE_AGENTS = 3
FIVE_PLAYER_GAME_SIZE = 5
MAX_DIRECT_QUESTIONS = 3
MAX_RECENT_PHRASES = 5
MAX_RECENT_VOTE_DECLARATIONS = 5
MAX_LEC_OTHER_MODELS = 4
FREEFORM_TALK_FALLBACK_LIMIT = 4
MIN_SUSPICIOUS_ASCII_WORDS = 2
MIN_COMPETING_SEER_CLAIMS = 2
SENTENCE_ENDINGS = ("。", "\N{FULLWIDTH EXCLAMATION MARK}", "\N{FULLWIDTH QUESTION MARK}", ".", "!", "?")
SOFT_BREAKS = " \n\t"
JAPANESE_ALLOWED_TEXT_PATTERN = re.compile(
    r"[^a-zA-Z0-9ａ-ｚＡ-Ｚ０-９ぁ-ん\u30A1-\u30FA一-龥々〇。、！？!?@\sー\uFF66-\uFF9F.,'’:;\-]",
)
ENGLISH_ALLOWED_TEXT_PATTERN = re.compile(r"[^A-Za-z0-9@.!?' :;\s\-]")
INVALID_MENTION_PATTERN = re.compile(r"@\s*([^\s.!?,;:'\"()]+)")
ENGLISH_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{EN DASH}": "-",
        "\N{EM DASH}": "-",
        "\N{FULLWIDTH FULL STOP}": ".",
        "\N{FULLWIDTH EXCLAMATION MARK}": "!",
        "\N{FULLWIDTH QUESTION MARK}": "?",
    },
)
PROTOCOL_OUTPUT_PATTERN = re.compile(
    r"^\s*(?:VOTE|ATTACK|DIVINE|GUARD|COMINGOUT|DIVINED|IDENTIFIED|ESTIMATE)"
    r"\s+(?:Agent\[\d+]|[A-Za-z][A-Za-z0-9_-]*)"
    r"(?:\s+(?:SEER|MEDIUM|BODYGUARD|VILLAGER|POSSESSED|WEREWOLF|HUMAN))?\s*[.;]?\s*$",
    re.IGNORECASE,
)
FENCED_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*.*?```", re.DOTALL | re.IGNORECASE)
FREEFORM_OUTPUT_LABEL_PATTERN = re.compile(
    r"^\s*(?:発言|出力|台詞|セリフ|utterance|speech|remark|response|text)\s*[:\N{FULLWIDTH COLON}]\s*",
    re.IGNORECASE,
)
FREEFORM_TEXT_JSON_KEYS = ("utterance", "speech", "talk", "text", "message")
# Meta / work-note leakage markers. Deliberately narrow so ordinary game talk
# (words like "yesterday" or "let's") is not falsely flagged.
LEAKED_WORK_NOTE_PATTERN = re.compile(
    r"\b(?:final\s+answer|let\s+me\s+think|as\s+an\s+ai|i\s+will\s+now|step\s+\d|"
    r"intent_type|reason_source|should_mention|analysis\s*:|reasoning\s*:|draft\s*:|my\s+response\s*:)\b",
    re.IGNORECASE,
)
ASCII_WORD_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z'?]{2,}\b")
JAPANESE_TEXT_PATTERN = re.compile(r"[ぁ-ん\u30A1-\u30FA一-龥々〇]")
ALLOWED_FREEFORM_ASCII_WORDS = frozenset({"co", "pp", "skip", "over"})


class Agent:
    """Base class for agents.

    エージェントの基底クラス.
    """

    def __init__(
        self,
        config: dict[str, Any],
        name: str,
        game_id: str,
        role: Role,
    ) -> None:
        """Initialize the agent.

        エージェントの初期化を行う.

        Args:
            config (dict[str, Any]): Configuration dictionary / 設定辞書
            name (str): Agent name / エージェント名
            game_id (str): Game ID / ゲームID
            role (Role): Role / 役職
        """
        self.config = config
        self.agent_name = name
        self.agent_logger = AgentLogger(config, name, game_id)
        self.request: Request | None = None
        self.info: Info | None = None
        self.profile: str | None = None
        self.setting: Setting | None = None
        self.talk_history: list[Talk] = []
        self.whisper_history: list[Talk] = []
        self.role = role
        # グループチャット方式
        self.in_talk_phase = False
        self.in_whisper_phase = False
        self.talk_phase_event = asyncio.Event()
        self.whisper_phase_event = asyncio.Event()

        self.sent_talk_count: int = 0
        self.sent_whisper_count: int = 0
        self.freeform_talk_send_count: int = 0
        self.freeform_whisper_send_count: int = 0
        self.llm_usage_total: dict[str, int] = {}
        self.llm_model: BaseChatModel | None = None

        load_dotenv(Path(__file__).parent.joinpath("./../../config/.env"))

    @staticmethod
    def timeout(func: Callable[P, T]) -> Callable[P, T]:
        """Decorator to set action timeout.

        アクションタイムアウトを設定するデコレータ.

        Args:
            func (Callable[P, T]): Function to be decorated / デコレート対象の関数

        Returns:
            Callable[P, T]: Function with timeout functionality / タイムアウト機能を追加した関数
        """

        def _wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            res: T | BaseException = Exception("No result")

            def execute_with_timeout() -> None:
                nonlocal res
                try:
                    res = func(*args, **kwargs)
                except KeyboardInterrupt as e:
                    res = e
                except Exception as e:  # noqa: BLE001
                    res = e

            thread = StoppableThread(target=execute_with_timeout)
            thread.start()
            self = args[0] if args else None
            if not isinstance(self, Agent):
                raise TypeError(self, " is not an Agent instance")
            timeout_value = self._action_timeout_seconds()
            if self._manual_llm_inspection_enabled(self.request):
                timeout_value = 0
            if timeout_value > 0:
                thread.join(timeout=timeout_value)
                if thread.is_alive():
                    return cast("T", self._handle_action_timeout(thread))
            else:
                thread.join()
            if isinstance(res, BaseException):  # type: ignore[arg-type]
                raise res
            return res

        return _wrapper

    def _action_timeout_seconds(self) -> float:
        """Return action timeout seconds with a safety margin before the server limit."""
        agent_config = self._as_dict(self.config.get("agent"))
        default_timeout = float(agent_config.get("default_action_timeout_seconds", 55))
        if not self.setting:
            return default_timeout

        server_timeout = float(self.setting.timeout.action) / 1000
        margin = float(agent_config.get("action_timeout_margin_seconds", 5))
        if server_timeout <= 0:
            return default_timeout
        if server_timeout <= margin:
            safe_timeout = max(server_timeout * 0.8, 0.1)
        else:
            safe_timeout = server_timeout - margin
        return min(safe_timeout, default_timeout) if default_timeout > 0 else safe_timeout

    def _handle_action_timeout(self, thread: StoppableThread) -> str | None:
        """Handle an action timeout and return a safe fallback response."""
        request_audit.timed_out(self.config, self.request)
        self.agent_logger.logger.warning(
            "Action timed out: %s",
            self.request,
        )
        if bool(self.config["agent"]["kill_on_timeout"]):
            thread.stop()
            thread.join(timeout=0.1)
            self.agent_logger.logger.warning(
                "Action was forcibly terminated: %s",
                self.request,
            )
        fallback = self._timeout_fallback()
        self.agent_logger.logger.warning(
            "Returning a fallback response due to timeout: %s -> %s",
            self.request,
            fallback,
        )
        return fallback

    def _fallback_action_target(self) -> str | None:
        """Return a safe target for action requests when normal processing fails."""
        alive_agents = self.get_alive_agents()
        valid_targets = [agent for agent in alive_agents if self.info and agent != self.info.agent]
        if not valid_targets:
            valid_targets = alive_agents
        return random.choice(valid_targets) if valid_targets else None  # noqa: S311

    def _timeout_fallback(self) -> str | None:
        """Return a request-specific fallback response after action timeout."""
        match self.request:
            case Request.NAME:
                return self.agent_name
            case Request.TALK | Request.WHISPER:
                return "Skip"
            case Request.VOTE | Request.DIVINE | Request.GUARD | Request.ATTACK:
                return self._fallback_action_target()
            case _:
                return None

    def set_packet(self, packet: Packet) -> None:
        """Set packet information.

        パケット情報をセットする.

        Args:
            packet (Packet): Received packet / 受信したパケット
        """
        self.request = packet.request
        if packet.info:
            self.info = packet.info
            if packet.info.profile is not None:
                self.profile = packet.info.profile
        if packet.setting:
            self.setting = packet.setting
        if packet.talk_history:
            self.talk_history = self._merge_history(self.talk_history, packet.talk_history)
        if packet.whisper_history:
            self.whisper_history = self._merge_history(self.whisper_history, packet.whisper_history)

        # グループチャット方式
        if packet.new_talk:
            self.talk_history = self._merge_history(self.talk_history, [packet.new_talk])
            self.on_talk_received(packet.new_talk)
            self.talk_phase_event.set()
        if packet.new_whisper:
            self.whisper_history = self._merge_history(self.whisper_history, [packet.new_whisper])
            self.on_whisper_received(packet.new_whisper)
            self.whisper_phase_event.set()

        if self.request == Request.INITIALIZE:
            self.talk_history: list[Talk] = []
            self.whisper_history: list[Talk] = []
            self.sent_talk_count = 0
            self.sent_whisper_count = 0
            self.freeform_talk_send_count = 0
            self.freeform_whisper_send_count = 0
            self.talk_phase_event.clear()
            self.whisper_phase_event.clear()
        elif self.request == Request.TALK_PHASE_START:
            self.freeform_talk_send_count = 0
        elif self.request == Request.WHISPER_PHASE_START:
            self.freeform_whisper_send_count = 0
        self._log_packet(packet)

    @staticmethod
    def _history_key(talk: Talk) -> tuple[int, int, int, str, str]:
        """Return a stable identity key for a talk or whisper entry.

        Args:
            talk (Talk): History entry.

        Returns:
            tuple[int, int, int, str, str]: Stable key.
        """

        def as_int(value: object, default: int = -1) -> int:
            if value is None:
                return default
            if isinstance(value, int):
                return value
            if not isinstance(value, str):
                return default
            with contextlib.suppress(TypeError, ValueError):
                return int(value)
            return default

        return (
            as_int(getattr(talk, "day", None)),
            as_int(getattr(talk, "idx", None)),
            as_int(getattr(talk, "turn", None)),
            str(getattr(talk, "agent", "")),
            str(getattr(talk, "text", "")),
        )

    @classmethod
    def _merge_history(cls, current: list[Talk], incoming: list[Talk]) -> list[Talk]:
        """Merge history entries while removing duplicates and keeping chronological order.

        Args:
            current (list[Talk]): Existing entries.
            incoming (list[Talk]): New entries from the server.

        Returns:
            list[Talk]: Deduplicated, sorted history.
        """
        merged: dict[tuple[int, int, int, str, str], Talk] = {}
        for talk in [*current, *incoming]:
            merged[cls._history_key(talk)] = talk
        return sorted(merged.values(), key=cls._history_key)

    def get_phase_interval(self, phase: str) -> float:
        """Return the interval for a freeform phase loop.

        グループチャット方式の発話ループ間隔を返す.

        Args:
            phase (str): Phase name / フェーズ名

        Returns:
            float: Loop interval in seconds / ループ間隔(秒)
        """
        freeform = self.config.get("freeform", {})
        interval = freeform.get(f"{phase}_interval", freeform.get("interval", 5))
        return max(float(interval), 0.1)

    def _end_talk_phase_on_skip_enabled(self) -> bool:
        """Return whether a Skip response should end the local talk loop."""
        freeform = self._as_dict(self.config.get("freeform"))
        return bool(freeform.get("end_talk_phase_on_skip", False))

    def _freeform_send_limit(self, request: Request) -> int:
        """Return the server-provided per-agent send limit for a freeform phase."""
        fallback = FREEFORM_TALK_FALLBACK_LIMIT if request == Request.TALK else 0
        if self.setting is None:
            return fallback
        phase_setting = self.setting.talk if request == Request.TALK else self.setting.whisper
        max_count = getattr(phase_setting, "max_count", None)
        per_agent = getattr(max_count, "per_agent", fallback)
        return max(int(per_agent), 0)

    def _can_send_freeform(self, request: Request) -> bool:
        """Return whether a freeform message can be sent without exceeding limits."""
        if self.info and self.info.remain_count is not None and self.info.remain_count <= 0:
            return False
        sent_count = (
            self.freeform_talk_send_count
            if request == Request.TALK
            else self.freeform_whisper_send_count
        )
        return sent_count < self._freeform_send_limit(request)

    def _record_freeform_send(self, request: Request) -> None:
        """Record one locally transmitted freeform utterance."""
        if request == Request.TALK:
            self.freeform_talk_send_count += 1
        else:
            self.freeform_whisper_send_count += 1

    @staticmethod
    def _freeform_wire_text(text: str) -> str:
        """Convert internal turn-based controls to valid freeform controls."""
        return "Over" if text == "Skip" else text

    def get_alive_agents(self) -> list[str]:
        """Get the list of alive agents.

        生存しているエージェントのリストを取得する.

        Returns:
            list[str]: List of alive agent names / 生存エージェント名のリスト
        """
        if not self.info:
            return []
        return [k for k, v in self.info.status_map.items() if v == Status.ALIVE]

    def on_talk_received(self, talk: Talk) -> None:
        """Called when a new talk is received (freeform mode).

        新しいトークを受信した時に呼ばれる (グループチャット方式用).

        Args:
            talk (Talk): Received talk / 受信したトーク
        """

    def on_whisper_received(self, whisper: Talk) -> None:
        """Called when a new whisper is received (freeform mode).

        新しい囁きを受信した時に呼ばれる (グループチャット方式用).

        Args:
            whisper (Talk): Received whisper / 受信した囁き
        """

    async def handle_talk_phase(self, send: Callable[[str], None]) -> None:
        """Handle talk phase in freeform mode.

        グループチャット方式でのトークフェーズ処理.

        Args:
            send (Callable[[str], None]): Send function / 送信関数
        """
        self.talk_phase_event.clear()
        
        # フェーズ開始時の初動をランダムにばらけさせる (2秒〜12秒)
        await asyncio.sleep(random.uniform(2.0, 12.0))
        
        while self.in_talk_phase:
            if not self._can_send_freeform(Request.TALK):
                break

            text = await asyncio.to_thread(self.talk)
            if not self.in_talk_phase:
                break
            wire_text = self._freeform_wire_text(text)
            if wire_text:
                self.agent_logger.packet(Request.TALK, wire_text)
                send(wire_text)
                if wire_text != "Over":
                    self._record_freeform_send(Request.TALK)
                if wire_text == "Over":
                    break
                    
            # APIスパムと発話回数の早期消費を防ぐための待機ロジック
            last_history_len = len(self.talk_history)
            self.talk_phase_event.clear()
            
            # 定期的に起きるまでの最大待機時間（15秒〜30秒のランダム）
            timeout = random.uniform(15.0, 30.0)
            
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self.talk_phase_event.wait(),
                    timeout=timeout,
                )
                
            # 他のエージェントの発言によってイベントが発火した場合、
            # 全員が同時にLLMを呼び出さないように長めのランダム遅延を入れる
            if len(self.talk_history) > last_history_len and self.in_talk_phase:
                await asyncio.sleep(random.uniform(5.0, 15.0))

    async def handle_whisper_phase(self, send: Callable[[str], None]) -> None:
        """Handle whisper phase in freeform mode.

        グループチャット方式での囁きフェーズ処理.

        Args:
            send (Callable[[str], None]): Send function / 送信関数
        """
        self.whisper_phase_event.clear()
        
        # 囁きフェーズ開始時の初動をランダムにばらけさせる (1秒〜8秒)
        await asyncio.sleep(random.uniform(1.0, 8.0))
        
        while self.in_whisper_phase:
            if not self._can_send_freeform(Request.WHISPER):
                break

            text = await asyncio.to_thread(self.whisper)
            if not self.in_whisper_phase:
                break
            wire_text = self._freeform_wire_text(text)
            if wire_text:
                self.agent_logger.packet(Request.WHISPER, wire_text)
                send(wire_text)
                if wire_text != "Over":
                    self._record_freeform_send(Request.WHISPER)
                if wire_text == "Over":
                    break
                    
            # APIスパムと発話回数の早期消費を防ぐための待機ロジック
            last_history_len = len(self.whisper_history)
            self.whisper_phase_event.clear()
            
            timeout = random.uniform(15.0, 30.0)
            
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self.whisper_phase_event.wait(),
                    timeout=timeout,
                )
                
            if len(self.whisper_history) > last_history_len and self.in_whisper_phase:
                await asyncio.sleep(random.uniform(5.0, 15.0))

    def _send_message_to_llm(self, request: Request | None) -> str | None:
        """Send message to LLM and get response.

        LLMにメッセージを送信して応答を取得する.

        Args:
            request (Request | None): The request type to process / 処理するリクエストタイプ

        Returns:
            str | None: LLM response or None if error occurred / LLMの応答またはエラー時はNone
        """
        if request is None:
            return None
        if request.lower() not in self.config["prompt"]:
            return None
        prompt_config = self._as_dict(self.config["prompt"])
        request_key = str(request.lower())
        prompt = prompt_config.get(f"{request_key}_compact", prompt_config[request_key])
        if float(self.config["llm"]["sleep_time"]) > 0:
            sleep(float(self.config["llm"]["sleep_time"]))
        speech_style = self._build_speech_style_instruction()
        game_context = self._build_game_context(request)
        key = {
            "info": self.info,
            "setting": self.setting,
            "talk_history": self.talk_history,
            "whisper_history": self.whisper_history,
            "role": self.role,
            "game_mastery": self._get_prompt_fragment("game_mastery_compact") or self._get_prompt_fragment("game_mastery"),
            "strategy": self._get_role_strategy(),
            "sent_talk_count": self.sent_talk_count,
            "sent_whisper_count": self.sent_whisper_count,
            "speech_style": speech_style,
            "game_context": game_context,
            "talk_context": self._format_history(self.talk_history),
            "whisper_context": self._format_history(self.whisper_history),
            "current_day_talk_context": self._format_history(self._current_day_history(self.talk_history)),
            "current_day_whisper_context": self._format_history(self._current_day_history(self.whisper_history)),
            "current_day_own_talk_context": self._format_history(self._own_current_day_talk_history()),
            "recent_own_talk_context": self._recent_own_talk_summary(),
        }
        template: Template = Template(prompt)
        prompt = template.render(**key).strip()
        prompt = f"{prompt}\n\n{game_context}" if game_context else prompt
        if self._uses_speech_style(request) and speech_style:
            prompt = f"{speech_style}\n\n{prompt}"
        if self.llm_model is None:
            self.agent_logger.logger.error("LLM is not initialized")
            return None
        admission = request_audit.begin(self.config, request)
        started_at = monotonic()
        try:
            self._inspect_llm_request(request, prompt)
            llm_response = self.llm_model.invoke([HumanMessage(content=prompt)])
            response = self._extract_response_text(llm_response)
            self._log_llm_usage(request, llm_response)
            if self._manual_llm_inspection_enabled(request):
                self.agent_logger.logger.info("LLM response received for %s", request)
            else:
                self.agent_logger.logger.info(["LLM", prompt, response])
            self._inspect_llm_response(request, prompt, response)
            self._log_llm_call(request, prompt, response, monotonic() - started_at, None, llm_response)
            request_audit.finish(admission)
        except Exception as error:
            self._log_llm_call(request, prompt, None, monotonic() - started_at, error, None)
            request_audit.finish(admission, error)
            self.agent_logger.logger.exception("Failed to send message to LLM")
            return None
        else:
            return response

    def _get_manual_llm_config(self) -> dict[str, Any]:
        """Return manual LLM inspection settings.

        Returns:
            dict[str, Any]: Manual LLM inspection config.
        """
        debug_config = self._as_dict(self.config.get("debug"))
        return self._as_dict(debug_config.get("manual_llm"))

    def _manual_llm_mode_enabled(self) -> bool:
        """Return whether manual LLM inspection mode is enabled.

        Returns:
            bool: True when manual LLM inspection mode is enabled.
        """
        return bool(self._get_manual_llm_config().get("enabled", False))

    def _log_packet(self, packet: Packet) -> None:
        """Log an incoming packet.

        Args:
            packet (Packet): Received packet.
        """
        if self._manual_llm_mode_enabled():
            self.agent_logger.logger.debug(
                "packet:\n%s",
                json.dumps(self._debug_serialize(packet), ensure_ascii=False, indent=2),
            )
            return
        self.agent_logger.logger.debug(packet)

    def _manual_llm_inspection_enabled(self, request: Request | None) -> bool:
        """Return whether this LLM call should be manually inspected.

        Args:
            request (Request | None): Current request.

        Returns:
            bool: True when manual inspection should pause this call.
        """
        manual_config = self._get_manual_llm_config()
        if not self._manual_llm_mode_enabled():
            return False
        if request is None:
            return False

        agents = {str(agent) for agent in self._as_list(manual_config.get("agents")) if str(agent)}
        game_agent = self.info.agent if self.info is not None else None
        if agents and self.agent_name not in agents and game_agent not in agents:
            return False

        requests = {str(item).lower() for item in self._as_list(manual_config.get("requests")) if str(item)}
        return not requests or str(request.lower()) in requests

    @staticmethod
    def _normalize_token_usage(value: object) -> dict[str, int]:
        """Normalize provider token usage metadata into a flat integer dictionary."""
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            with contextlib.suppress(Exception):
                value = value.model_dump()
        if not isinstance(value, dict):
            return {}

        usage: dict[str, int] = {}
        for key, raw_value in value.items():
            if isinstance(raw_value, int):
                usage[str(key)] = raw_value
            elif isinstance(raw_value, dict):
                for nested_key, nested_value in raw_value.items():
                    if isinstance(nested_value, int):
                        usage[f"{key}.{nested_key}"] = nested_value
        return usage

    @staticmethod
    def _extract_response_text(response: object) -> str:
        """Extract text content from a LangChain chat response."""
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return str(content)

    def _log_llm_usage(self, request: Request, response: object) -> None:
        """Log per-call and cumulative LLM token usage when provider metadata is available."""
        response_metadata = getattr(response, "response_metadata", None)
        usage_metadata = getattr(response, "usage_metadata", None)
        usage = self._normalize_token_usage(usage_metadata)
        if not usage and isinstance(response_metadata, dict):
            usage = self._normalize_token_usage(
                response_metadata.get("token_usage")
                or response_metadata.get("usage")
                or response_metadata.get("usage_metadata"),
            )
        if not usage:
            self.agent_logger.logger.info(
                [
                    "LLM_USAGE",
                    {
                        "request": self._value_text(request),
                        "available": False,
                        "model": self._llm_debug_model_config(),
                    },
                ],
            )
            return

        for key, value in usage.items():
            self.llm_usage_total[key] = self.llm_usage_total.get(key, 0) + value

        payload = {
            "agent": self.agent_name,
            "game_id": self._value_text(getattr(self.info, "game_id", "")),
            "day": getattr(self.info, "day", None),
            "request": self._value_text(request),
            "condition": str(self._experiment_config().get("condition", "")),
            "model": self._llm_debug_model_config(),
            "response_model_id": self._response_model_id(response),
            "usage": usage,
            "total": dict(sorted(self.llm_usage_total.items())),
        }
        self.agent_logger.llm_usage(payload)

    def _log_llm_call(
        self,
        request: Request,
        prompt: str,
        response: str | None,
        latency_seconds: float,
        error: Exception | None,
        response_object: object | None,
    ) -> None:
        """Record every observable call, including failures without usage metadata."""
        payload = {
            "agent": self.agent_name,
            "game_id": self._value_text(getattr(self.info, "game_id", "")),
            "day": getattr(self.info, "day", None),
            "request": self._value_text(request),
            "condition": str(self._experiment_config().get("condition", "")),
            "model": self._llm_debug_model_config(),
            "response_model_id": self._response_model_id(response_object),
            "success": error is None,
            "error_type": type(error).__name__ if error is not None else None,
            "latency_seconds": round(latency_seconds, 6),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "response_sha256": hashlib.sha256(response.encode()).hexdigest() if response is not None else None,
            "retry_count_observable": 0,
            "configured_max_retries": int(self._as_dict(self.config.get("openai")).get("max_retries", 0)),
        }
        self.agent_logger.llm_call(payload)

    @staticmethod
    def _response_model_id(response: object | None) -> str | None:
        """Extract the provider-reported model ID when LangChain exposes it."""
        if response is None:
            return None
        metadata = getattr(response, "response_metadata", None)
        if not isinstance(metadata, dict):
            return None
        for key in ("model_name", "model", "model_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _inspect_llm_request(self, request: Request, prompt: str) -> None:
        """Print the exact message payload before sending it to the LLM.

        Args:
            request (Request): Current request.
            prompt (str): Rendered prompt text.
        """
        manual_config = self._get_manual_llm_config()
        if not self._manual_llm_inspection_enabled(request):
            return
        if not bool(manual_config.get("pause_before_request", True)):
            return

        payload = {
            "stage": "before_llm_request",
            "request": self._value_text(request),
            "model": self._llm_debug_model_config(),
            "agent_state": self._llm_debug_agent_state(),
            "messages": [
                {
                    "type": "HumanMessage",
                    "content": prompt,
                },
            ],
        }
        self._manual_pause(
            title="Before LLM API call",
            payload=payload,
            continue_label="Press Enter to send this content to the API",
        )

    def _inspect_llm_response(self, request: Request, prompt: str, response: str) -> None:
        """Print the LLM response before continuing the game.

        Args:
            request (Request): Current request.
            prompt (str): Rendered prompt text.
            response (str): Raw LLM response.
        """
        manual_config = self._get_manual_llm_config()
        if not self._manual_llm_inspection_enabled(request):
            return
        if not bool(manual_config.get("pause_after_response", True)):
            return

        payload = {
            "stage": "after_llm_response",
            "request": self._value_text(request),
            "model": self._llm_debug_model_config(),
            "agent_state": self._llm_debug_agent_state(),
            "messages": [
                {
                    "type": "HumanMessage",
                    "content": prompt,
                },
            ],
            "response": response,
        }
        self._manual_pause(
            title="After LLM API response",
            payload=payload,
            continue_label="Press Enter to continue game processing",
        )

    def _manual_pause(self, title: str, payload: dict[str, Any], continue_label: str) -> None:
        """Show a debug payload and wait for user confirmation.

        Args:
            title (str): Display title.
            payload (dict[str, Any]): Payload to display.
            continue_label (str): Prompt shown before waiting.
        """
        if not sys.stdin.isatty():
            self.agent_logger.logger.warning("manual_llm is enabled, but stdin is not interactive. Continuing.")
            return

        sys.stderr.write("\n" + "=" * 80 + "\n")
        sys.stderr.write(f"[{self.agent_name}] {title}\n")
        sys.stderr.write("=" * 80 + "\n")
        self._write_manual_payload(payload)
        sys.stderr.write("=" * 80 + "\n")
        while True:
            try:
                command = input(f"{continue_label} / q to quit > ").strip().lower()
            except EOFError:
                self.agent_logger.logger.warning("manual_llm input reached EOF. Continuing.")
                return
            if command in {"", "c", "continue"}:
                return
            if command in {"q", "quit", "exit"}:
                raise KeyboardInterrupt
            sys.stderr.write("Press Enter or c to continue, q to quit.\n")

    def _write_manual_payload(self, payload: dict[str, Any]) -> None:
        """Write manual inspection payload in a readable, sectioned format.

        Args:
            payload (dict[str, Any]): Payload to display.
        """
        messages = self._as_list(payload.get("messages"))
        response = payload.get("response")
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"agent_state", "messages", "response"}
        }

        self._write_manual_json_section("Metadata", metadata)
        self._write_manual_json_section("Agent state", payload.get("agent_state"))
        for index, message in enumerate(messages, start=1):
            message_dict = self._as_dict(message)
            message_type = message_dict.get("type", "Message")
            content = message_dict.get("content", "")
            self._write_manual_text_section(
                f"Sent message {index}: {message_type}",
                str(content),
            )
        if response is not None:
            self._write_manual_text_section("Raw LLM response", str(response))

    def _write_manual_json_section(self, title: str, value: object) -> None:
        """Write one JSON section for manual inspection.

        Args:
            title (str): Section title.
            value (object): Value to serialize.
        """
        sys.stderr.write(f"\n--- {title} ---\n")
        sys.stderr.write(json.dumps(self._debug_serialize(value), ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _write_manual_text_section(title: str, text: str) -> None:
        """Write one raw text section for manual inspection.

        Args:
            title (str): Section title.
            text (str): Text to display without JSON escaping.
        """
        sys.stderr.write(f"\n--- {title} ---\n")
        sys.stderr.write(text.rstrip() + "\n")

    def _llm_debug_model_config(self) -> dict[str, str]:
        """Return configured and effective LLM information for debug output.

        Returns:
            dict[str, str]: Model configuration summary.
        """
        model_type = str(self.config.get("llm", {}).get("type", ""))
        provider_config = self._as_dict(self.config.get(model_type))
        effective_temperature = getattr(self.llm_model, "temperature", None)
        return {
            "type": model_type,
            "model": str(provider_config.get("model", "")),
            "temperature": str(provider_config.get("temperature", "")),
            "effective_temperature": str(effective_temperature) if effective_temperature is not None else "",
            "max_retries": str(provider_config.get("max_retries", "")),
        }

    def _llm_debug_agent_state(self) -> dict[str, Any]:
        """Return a serializable snapshot of the agent context.

        Returns:
            dict[str, Any]: Agent state used to build the prompt.
        """
        return {
            "client_agent_name": self.agent_name,
            "game_agent_name": self.info.agent if self.info is not None else None,
            "role": self.role,
            "request": self.request,
            "profile": self.profile,
            "info": self.info,
            "setting": self.setting,
            "talk_history": self.talk_history,
            "whisper_history": self.whisper_history,
            "sent_talk_count": self.sent_talk_count,
            "sent_whisper_count": self.sent_whisper_count,
        }

    @classmethod
    def _debug_serialize(cls, value: object) -> object:  # noqa: PLR0911
        """Convert packet/model objects to JSON-serializable debug data.

        Args:
            value (object): Value to convert.

        Returns:
            object: JSON-serializable representation.
        """
        enum_value = getattr(value, "value", None)
        if enum_value is not None and isinstance(enum_value, str | int | float | bool):
            return enum_value
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, dict):
            raw_dict = cast("dict[object, object]", value)
            return {str(key): cls._debug_serialize(dict_value) for key, dict_value in raw_dict.items()}
        if isinstance(value, list | tuple | set):
            raw_sequence = cast("list[object] | tuple[object, ...] | set[object]", value)
            return [cls._debug_serialize(item) for item in raw_sequence]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            with contextlib.suppress(Exception):
                return cls._debug_serialize(model_dump(mode="json"))
            with contextlib.suppress(Exception):
                return cls._debug_serialize(model_dump())
        if hasattr(value, "__dict__"):
            return {
                str(key): cls._debug_serialize(dict_value)
                for key, dict_value in vars(value).items()
                if not str(key).startswith("_")
            }
        return str(value)

    @staticmethod
    def _value_text(value: object) -> str:
        """Return a readable representation for enum-like and arbitrary values."""
        enum_value = getattr(value, "value", None)
        return str(enum_value if enum_value is not None else value)

    def _format_history(self, history: list[Talk]) -> str:
        """Format deduplicated history for prompts.

        Args:
            history (list[Talk]): Talk or whisper entries.

        Returns:
            str: Prompt-ready history text.
        """
        if not history:
            return "None"
        lines: list[str] = []
        for talk in sorted(history, key=self._history_key):
            day = getattr(talk, "day", "?")
            agent = getattr(talk, "agent", "?")
            text = getattr(talk, "text", "")
            flags: list[str] = []
            if bool(getattr(talk, "skip", False)):
                flags.append("skipped their turn")
            if bool(getattr(talk, "over", False)):
                flags.append("said they would not talk further")
            flag_text = f" ({'; '.join(flags)})" if flags else ""
            lines.append(f"Day {day}, {agent}: {text}{flag_text}")
        return "\n".join(lines)

    def _current_day_history(self, history: list[Talk]) -> list[Talk]:
        """Return entries from the current day only."""
        if self.info is None:
            return history
        current_day = self.info.day
        return [talk for talk in history if getattr(talk, "day", None) == current_day]

    def _own_current_day_talk_history(self) -> list[Talk]:
        """Return this agent's talk entries from the current day."""
        if self.info is None:
            return []
        return [
            talk
            for talk in self._current_day_history(self.talk_history)
            if getattr(talk, "agent", None) == self.info.agent
        ]

    def _should_skip_day0_after_greeting(self) -> bool:
        """Return whether day 0 talk should be skipped after this agent has greeted."""
        if self.info is None:
            return False
        if self._value_text(self.info.day) != "0":
            return False
        return bool(self._own_current_day_talk_history())

    def _recent_own_talk_summary(self, limit: int = 3) -> str:
        """Return a compact summary of this agent's recent own talks."""
        own_talks = self._own_current_day_talk_history()
        if not own_talks:
            return "None"
        return self._format_history(own_talks[-limit:])

    def _alive_agent_names(self, info: Info) -> list[str]:
        """Return alive agent names from the current info."""
        return [
            agent
            for agent, status in info.status_map.items()
            if self._value_text(status) == self._value_text(Status.ALIVE)
        ]

    def _extract_seer_claims(self) -> set[str]:
        """Extract public seer claimants from talk history."""
        claimants: set[str] = set()
        for talk in self.talk_history:
            agent = str(getattr(talk, "agent", ""))
            text = str(getattr(talk, "text", ""))
            if agent and re.search(
                r"\b(?:i\s*am|i'?m|im|this\s+is|as)\s+(?:the\s+)?seer\b"
                r"|\bmy\s+(?:divine|divination|reading)\b",
                text,
                re.IGNORECASE,
            ):
                claimants.add(agent)
        return claimants

    def _extract_divination_claims(self, seer_claimants: set[str], agent_names: list[str]) -> dict[str, dict[str, str]]:
        """Extract public divination claims by seer claimant.

        The parser is intentionally conservative: result-like text from non-seer
        claimants is ignored so discussion such as "X真ならY白" is not treated
        as a new report.
        """
        claims: dict[str, dict[str, str]] = {seer: {} for seer in seer_claimants}
        if not seer_claimants or not agent_names:
            return claims

        name_pattern = "|".join(re.escape(name) for name in sorted(agent_names, key=len, reverse=True))
        result_word = r"white|black|human|werewolf"
        result_patterns = [
            # "white on Alex", "black result for Bob"
            re.compile(
                rf"\b(?P<result>{result_word})\b(?:\s+result)?\s*(?:on|for|to|,|:)?\s*(?P<target>{name_pattern})\b",
                re.IGNORECASE,
            ),
            # "Alex is white", "Bob came back black", "Alex: white"
            re.compile(
                rf"\b(?P<target>{name_pattern})\b\s*"
                r"(?:is|was|came\s+back|turned\s+out(?:\s+to\s+be)?|=|:|,)?\s*(?:a\s+)?"
                rf"(?P<result>{result_word})\b",
                re.IGNORECASE,
            ),
        ]
        for talk in sorted(self.talk_history, key=self._history_key):
            reporter = str(getattr(talk, "agent", ""))
            if reporter not in seer_claimants:
                continue
            text = str(getattr(talk, "text", ""))
            for result_pattern in result_patterns:
                for match in result_pattern.finditer(text):
                    target = match.group("target")
                    if target == reporter:
                        continue
                    result = match.group("result").upper()
                    claims.setdefault(reporter, {})[target] = (
                        "black" if result in {"BLACK", "WEREWOLF"} else "white"
                    )
        return claims

    def _build_perspective_context(self, info: Info) -> str:
        """Build a prompt context for per-seer gray lists."""
        alive_agents = self._alive_agent_names(info)
        seer_claimants = self._extract_seer_claims()
        if not alive_agents or not seer_claimants:
            return ""

        divination_claims = self._extract_divination_claims(seer_claimants, list(info.status_map))
        lines = [
            "[Public information organized by viewpoint]",
            "If there are multiple seer COs, a white result is white only from that seer's viewpoint.",
            "Do not remove a rival seer's white target from another seer's grays.",
        ]
        role_claimants = seer_claimants
        for seer in sorted(seer_claimants):
            judged = divination_claims.get(seer, {})
            judged_text = ", ".join(f"{target}: {result}" for target, result in judged.items()) or "none"
            gray_agents = [
                agent
                for agent in alive_agents
                if agent != seer and agent not in role_claimants and agent not in judged
            ]
            gray_text = ", ".join(gray_agents) or "none"
            lines.append(f"- From {seer}'s viewpoint: judged={judged_text}; grays={gray_text}.")
        return "\n".join(lines)

    def _build_endgame_context(self, info: Info) -> str:
        """Build compact reminders for late-game reasoning."""
        alive_agents = self._alive_agent_names(info)
        if len(alive_agents) > MAX_ENDGAME_ALIVE_AGENTS:
            return ""

        lines = [
            "[Endgame notes]",
            "Few players remain, so don't end on questions alone; prioritize your vote target and reason.",
            "Name the player who looks most like a werewolf from your viewpoint, or who should be executed.",
        ]
        if len(alive_agents) == MAX_ENDGAME_ALIVE_AGENTS and self._pp_instruction_enabled():
            lines.append("At three survivors, always confirm whether it is a wolf/possessed/villager PP board.")
            if self.role == Role.POSSESSED:
                lines.append(
                    "You are the possessed. If a wolf/possessed/villager board is likely, "
                    "declare possessed and call for the werewolf to match votes.",
                )
            if self.role == Role.WEREWOLF:
                lines.append(
                    "You are the werewolf. If a possessed CO appears, declare werewolf "
                    "and match votes onto the villager candidate.",
                )
            if self.role == Role.VILLAGER:
                lines.append(
                    "You are villager-side. Don't stop at a vote declaration; cite an element of "
                    "the wolf candidate and aim to make the possessed misjudge.",
                )
        if len(info.status_map) == FIVE_PLAYER_GAME_SIZE:
            lines.append(
                "In a 5-player endgame, reason from the attacked seer candidate, white results, "
                "black results, and the previous day's votes together.",
            )

        attacked_agent = getattr(info, "attacked_agent", None)
        if attacked_agent is not None:
            attacked_text = str(attacked_agent)
            seer_claimants = self._extract_seer_claims()
            if attacked_text in seer_claimants:
                remaining_seers = [
                    seer
                    for seer in seer_claimants
                    if seer in alive_agents and seer != attacked_text
                ]
                remaining_text = ", ".join(sorted(remaining_seers)) or "none"
                lines.append(
                    f"The attacked seer candidate is {attacked_text}. "
                    f"Surviving seer candidates: {remaining_text}.",
                )
        return "\n".join(lines)

    def _build_repeated_phrase_context(self) -> str:
        """Build reminders to avoid copied or repetitive phrasing."""
        current_day_history = self._current_day_history(self.talk_history)
        if not current_day_history:
            return ""

        watched_phrases = [
            "i admit my explanation is short",
            "i admit my reply is short",
            "i admit rushing",
            "the result and remarks match",
            "seems a little strange",
        ]
        used: list[str] = []
        for talk in current_day_history:
            text = str(getattr(talk, "text", "")).lower()
            for phrase in watched_phrases:
                if phrase in text and phrase not in used:
                    used.append(phrase)

        if not used:
            return ""

        phrase_text = ", ".join(used[:MAX_RECENT_PHRASES])
        return "\n".join(
            [
                "[Note on repeated phrasing]",
                f"Phrases already used today: {phrase_text}",
                "Avoid the same phrasing and add one reason of your own.",
            ],
        )

    def _build_fake_seer_consistency_context(self, info: Info) -> str:
        """Build reminders that prevent fake seer result contradictions."""
        if self.role != Role.POSSESSED:
            return ""
        current_agent = str(info.agent)
        alive_agents = self._alive_agent_names(info)
        if not current_agent or current_agent not in alive_agents:
            return ""

        seer_claimants = self._extract_seer_claims()
        if current_agent not in seer_claimants:
            return ""

        claims = self._extract_divination_claims({current_agent}, list(info.status_map))
        own_claims = claims.get(current_agent, {})
        alive_white_targets = [
            target
            for target, result in own_claims.items()
            if result == "white" and target in alive_agents
        ]
        if not alive_white_targets:
            return ""

        white_text = ", ".join(sorted(alive_white_targets))
        lines = [
            "[Fake seer result consistency]",
            f"Among survivors you have already given white to: {white_text}.",
            "Giving white to too many survivors erases the wolf position from your viewpoint and breaks down.",
        ]
        if self._pp_instruction_enabled():
            lines.append("At three survivors, prioritize PP judgment and matching votes over a new white result.")
        return "\n".join(lines)

    def _build_direct_question_context(self, info: Info) -> str:
        """Build a compact list of recent direct questions addressed to this agent."""
        current_agent = str(info.agent)
        if not current_agent:
            return ""

        direct_questions: list[str] = []
        name_pattern = re.compile(
            rf"(?:@\s*{re.escape(current_agent)}\b|\b{re.escape(current_agent)}\b)",
            re.IGNORECASE,
        )
        question_pattern = re.compile(
            r"(?:\?|？|\bwho\b|\bwhy\b|\bwhat\b|\bhow\b|\bwhich\b|\breason\b|tell\s+me|answer)",
            re.IGNORECASE,
        )
        for talk in self._current_day_history(self.talk_history):
            speaker = str(getattr(talk, "agent", ""))
            if speaker == current_agent:
                continue
            text = str(getattr(talk, "text", ""))
            if name_pattern.search(text) and question_pattern.search(text):
                direct_questions.append(f"- from {speaker}: {text}")

        if not direct_questions:
            return ""

        lines = [
            "[Recent questions addressed to you]",
            "Leaving questions unanswered draws suspicion, so answer with your conclusion first next turn.",
            "Here, don't ask a question back; state only your answer, reason, and vote candidate.",
            'When you need to make the addressee clear, follow a short public remark with "@asker answer".',
            *direct_questions[-MAX_DIRECT_QUESTIONS:],
        ]
        return "\n".join(lines)

    def _build_vote_declaration_context(self, info: Info) -> str:
        """Build a compact list of current-day vote declarations."""
        alive_agents = self._alive_agent_names(info)
        if not alive_agents:
            return ""

        declaration_pattern = re.compile(
            r"(?:\bi'?ll\s+vote\b|\bi\s+vote\b|\bvoting\s+for\b|\bvote\s+for\b|\bmy\s+vote\b|"
            r"\blet'?s\s+vote\b|\bexecute\b|\bhang\b|\blynch\b)",
            re.IGNORECASE,
        )
        latest_declarations: dict[str, str] = {}
        for talk in self._current_day_history(self.talk_history):
            speaker = str(getattr(talk, "agent", ""))
            if speaker not in alive_agents:
                continue
            text = str(getattr(talk, "text", ""))
            if not declaration_pattern.search(text):
                continue
            mentioned_targets = self._declared_vote_targets(text, speaker, alive_agents)
            if mentioned_targets:
                latest_declarations[speaker] = f"- {speaker}: {text}"

        if not latest_declarations:
            return ""

        lines = [
            "[Today's vote declarations]",
            "When you cite a vote target or its absence, never contradict these latest remarks.",
            "Do not say a player listed here has no vote target or won't reveal one.",
            *list(latest_declarations.values())[-MAX_RECENT_VOTE_DECLARATIONS:],
        ]
        return "\n".join(lines)

    @staticmethod
    def _declared_vote_targets(text: str, speaker: str, alive_agents: list[str]) -> list[str]:
        """Return names that are close to vote or execution words in a talk."""
        targets = [agent for agent in alive_agents if agent != speaker]
        if not targets:
            return []

        name_pattern = "|".join(re.escape(agent) for agent in sorted(targets, key=len, reverse=True))
        action_pattern = r"(?:vote|votes|voting|voted|execute|hang|lynch)"
        close_patterns = [
            re.compile(rf"(?P<target>{name_pattern}).{{0,20}}{action_pattern}", re.IGNORECASE),
            re.compile(rf"{action_pattern}.{{0,20}}(?P<target>{name_pattern})", re.IGNORECASE),
        ]
        found: list[str] = []
        for pattern in close_patterns:
            for match in pattern.finditer(text):
                target = match.group("target")
                if target not in found:
                    found.append(target)
        if found:
            return found

        return [
            agent
            for agent in targets
            if re.search(rf"(?:@\s*)?\b{re.escape(agent)}\b", text, re.IGNORECASE)
        ]

    def _latest_current_day_text_by_agent(self, agent: str) -> str:
        """Return the latest current-day talk text from an agent."""
        for talk in reversed(self._current_day_history(self.talk_history)):
            if str(getattr(talk, "agent", "")) == agent:
                return str(getattr(talk, "text", ""))
        return ""

    def _agent_vote_intention_note(self, agent: str, alive_agents: list[str]) -> str:
        """Infer a compact vote intention note from the agent's latest current-day talks."""
        declaration_pattern = re.compile(
            r"(?:\bi'?ll\s+vote\b|\bi\s+vote\b|\bvoting\s+for\b|\bvote\s+for\b|\bmy\s+vote\b|"
            r"\blet'?s\s+vote\b|\bexecute\b|\bhang\b|\blynch\b)",
            re.IGNORECASE,
        )
        for talk in reversed(self._current_day_history(self.talk_history)):
            speaker = str(getattr(talk, "agent", ""))
            if speaker != agent:
                continue
            text = str(getattr(talk, "text", ""))
            if not declaration_pattern.search(text):
                continue
            mentioned_targets = self._declared_vote_targets(text, agent, alive_agents)
            if mentioned_targets:
                return f"intends to vote for {mentioned_targets[0]}"
        return ""

    def _agent_public_belief_note(
        self,
        agent: str,
        seer_claimants: set[str],
        divination_claims: dict[str, dict[str, str]],
    ) -> str:
        """Build a compact public-belief note for one agent."""
        notes: list[str] = []
        if agent in seer_claimants:
            own_claims = divination_claims.get(agent, {})
            if own_claims:
                judged_text = ", ".join(f"{target}: {result}" for target, result in own_claims.items())
                notes.append(f"has claimed seer and reports {judged_text}")
            else:
                notes.append("has claimed seer")

        reported_by = [
            f"{result} by {seer}"
            for seer, judged in divination_claims.items()
            for target, result in judged.items()
            if target == agent
        ]
        if reported_by:
            notes.append("judged as " + ", ".join(reported_by))

        return "; ".join(notes) if notes else "little public role information"

    def _agent_talk_intention_note(self, agent: str, alive_agents: list[str], current_agent: str) -> str:
        """Infer a compact intention note from recent talk."""
        vote_note = self._agent_vote_intention_note(agent, alive_agents)
        if vote_note:
            return vote_note

        latest_text = self._latest_current_day_text_by_agent(agent)
        if not latest_text:
            return "intent for today still unclear"
        suspect_re = (
            r"(?:\bvote\b|\bsuspect\b|suspicious|\bblack\b|\bexecute\b|\bhang\b|\blynch\b|"
            r"\bweak\b|\bwolfish\b|doubt|bothers|not\s+enough|\bthin\b|late\s+reply)"
        )
        question_re = r"(?:\?|\bwho\b|\bwhy\b|\bwhat\b|\bhow\b|\bwhich\b|\breason\b|tell\s+me|answer)"
        if current_agent and re.search(
            rf"(?:@\s*)?\b{re.escape(current_agent)}\b", latest_text, re.IGNORECASE,
        ):
            if re.search(suspect_re, latest_text, re.IGNORECASE):
                return "intent to suspect you"
            if re.search(question_re, latest_text, re.IGNORECASE):
                return "intent to question you"
        if re.search(question_re, latest_text, re.IGNORECASE):
            return "intent to draw out information with questions"
        trust_re = r"(?:\bwhite\b|\btrue\b|\btrust\b|\bbelieve\b|villager-side|\btown\b)"
        if re.search(trust_re, latest_text, re.IGNORECASE):
            return "intent to lock in a trusted or white position"
        if re.search(suspect_re, latest_text, re.IGNORECASE):
            return "intent to build a suspect or execution target"
        return "intent to react or hold"

    def _build_lightweight_lec_context(self, info: Info) -> str:
        """Build lightweight LEC-style talk context without additional LLM calls."""
        if self.request != Request.TALK:
            return ""

        alive_agents = self._alive_agent_names(info)
        current_agent = str(info.agent)
        other_agents = [agent for agent in alive_agents if agent != current_agent]
        if not other_agents:
            return ""

        seer_claimants = self._extract_seer_claims()
        divination_claims = self._extract_divination_claims(seer_claimants, list(info.status_map))
        lines = [
            "[Lightweight LEC context]",
            "The following is an internal summary built mechanically from public history. "
            "Do not output headers or JSON in your utterance; use natural sentences only.",
            "Models of others:",
        ]
        for agent in other_agents[:MAX_LEC_OTHER_MODELS]:
            belief = self._agent_public_belief_note(agent, seer_claimants, divination_claims)
            desire = "villager-side wants to execute wolves; werewolf-side wants a misexecution or vote matching"
            intention = self._agent_talk_intention_note(agent, alive_agents, current_agent)
            lines.append(f"- {agent}: belief={belief}; desire={desire}; intent={intention}.")

        self_intentions: list[str] = []
        if self._build_own_black_context(info):
            self_intentions.append("DEFEND: rebut the black result against you and show an alternative vote target")
        if self._build_suspicion_context(info):
            self_intentions.append(
                "DEFEND: answer the suspicion of you briefly and give a reason for another candidate",
            )
        if self._build_direct_question_context(info):
            self_intentions.append("ANSWER: answer the question conclusion-first and show a vote candidate if needed")
        if len(alive_agents) <= MAX_ENDGAME_ALIVE_AGENTS:
            self_intentions.append("VOTE_DECLARE: it's the endgame, so prioritize your vote target and reason")
        if not self_intentions:
            self_intentions.append("ESTIMATE: state just one suspicion or trust based on public information")
            self_intentions.append("QUESTION: briefly ask someone whose vote target or result reasoning is unclear")

        lines.extend(
            [
                "Self-intent candidates:",
                *[f"- {intention}" for intention in self_intentions[:MAX_DIRECT_QUESTIONS]],
                "In your final utterance, pick just one self-intent candidate above and respond to others' intents.",
            ],
        )
        return "\n".join(lines)

    def _build_own_black_context(self, info: Info) -> str:
        """Build a compact context when this agent has publicly received a black result."""
        current_agent = str(info.agent)
        if not current_agent:
            return ""

        seer_claimants = self._extract_seer_claims()
        if not seer_claimants:
            return ""

        claims = self._extract_divination_claims(seer_claimants, list(info.status_map))
        black_reporters = [
            seer
            for seer, judged in claims.items()
            if judged.get(current_agent) == "black"
        ]
        if not black_reporters:
            return ""

        other_seers = [
            seer
            for seer in seer_claimants
            if seer not in black_reporters and seer != current_agent
        ]
        black_text = ", ".join(sorted(black_reporters))
        other_text = ", ".join(sorted(other_seers)) or "none"
        lines = [
            "[Black result against you]",
            f"{black_text} has given you a black result.",
            f"Other seer candidates: {other_text}.",
            "Posing as a villager, briefly state the fake elements of the seer who gave black "
            "and the danger of executing you.",
        ]
        return "\n".join(lines)

    def _build_suspicion_context(self, info: Info) -> str:
        """Build a compact context when this agent is being suspected or voted."""
        current_agent = str(info.agent)
        if not current_agent:
            return ""

        suspicion_lines: list[str] = []
        name_pattern = re.compile(
            rf"(?:@\s*{re.escape(current_agent)}\b|\b{re.escape(current_agent)}\b)",
            re.IGNORECASE,
        )
        suspicion_pattern = re.compile(
            r"(?:\bvote\b|\bsuspect\b|suspicious|\bblack\b|\bexecute\b|\bhang\b|\blynch\b|"
            r"\bweak\b|\bwolfish\b|doubt|bothers|not\s+enough|\bthin\b|late\s+reply)",
            re.IGNORECASE,
        )
        for talk in self._current_day_history(self.talk_history):
            speaker = str(getattr(talk, "agent", ""))
            if speaker == current_agent:
                continue
            text = str(getattr(talk, "text", ""))
            if name_pattern.search(text) and suspicion_pattern.search(text):
                suspicion_lines.append(f"- from {speaker}: {text}")

        if not suspicion_lines:
            return ""

        lines = [
            "[Suspicion against you]",
            "Don't leave suspicion unanswered; lightly admit any lack of reply, "
            "then show another vote candidate and reason.",
            "When suspected, avoid asking a question back; shift suspicion to a different gray candidate.",
            *suspicion_lines[-MAX_DIRECT_QUESTIONS:],
        ]
        return "\n".join(lines)

    def _build_game_context_header(self, request: Request, info: Info) -> list[str]:
        """Build current game state lines for the context block."""
        request_names = {
            Request.TALK: "daytime conversation",
            Request.WHISPER: "werewolf discussion",
            Request.VOTE: "voting",
            Request.ATTACK: "attack",
            Request.DIVINE: "divination",
            Request.GUARD: "guarding",
            Request.DAILY_INITIALIZE: "start of the day",
            Request.DAILY_FINISH: "end of the day",
        }
        role_names = {
            Role.SEER: "seer",
            Role.WEREWOLF: "werewolf",
            Role.POSSESSED: "possessed",
            Role.VILLAGER: "villager",
            Role.MEDIUM: "medium",
            Role.BODYGUARD: "bodyguard",
        }
        request_name = request_names.get(request, self._value_text(request))
        role_name = role_names.get(self.role, self._value_text(self.role))
        lines = [
            "[Current game context]",
            f"This is the {request_name} scene.",
            f"You are {info.agent}, and your role is the {role_name}.",
            f"Today is day {info.day}.",
        ]
        if self.profile:
            lines.append(
                f"Your profile is as follows: {self.profile} "
                "Use the profile as a reference for how you talk, "
                "not as a reason to suspect others.",
            )
        if info.status_map:
            alive_agents = [
                agent
                for agent, status in info.status_map.items()
                if self._value_text(status) == self._value_text(Status.ALIVE)
            ]
            dead_agents = [
                agent
                for agent, status in info.status_map.items()
                if self._value_text(status) != self._value_text(Status.ALIVE)
            ]
            if alive_agents:
                lines.append(f"Alive players: {', '.join(alive_agents)}.")
            if dead_agents:
                lines.append(f"Already dead players: {', '.join(dead_agents)}.")
        optional_fields = [
            ("Divine result:", info.divine_result),
            ("Medium result:", info.medium_result),
            ("Most recently executed:", info.executed_agent),
            ("Most recently attacked:", info.attacked_agent),
            ("Vote result:", info.vote_list),
            ("Attack vote result:", info.attack_vote_list),
            ("Remaining talk count:", info.remain_count),
        ]
        lines.extend(f"{label} {value}." for label, value in optional_fields if value is not None)
        if not self._dynamic_context_enabled():
            return lines

        perspective_context = self._build_perspective_context(info)
        if perspective_context:
            lines.extend(["", perspective_context])
        endgame_context = self._build_endgame_context(info)
        if endgame_context:
            lines.extend(["", endgame_context])
        fake_seer_consistency_context = self._build_fake_seer_consistency_context(info)
        if fake_seer_consistency_context:
            lines.extend(["", fake_seer_consistency_context])
        repeated_phrase_context = self._build_repeated_phrase_context()
        if repeated_phrase_context:
            lines.extend(["", repeated_phrase_context])
        if request == Request.TALK:
            lightweight_lec_context = self._build_lightweight_lec_context(info)
            if lightweight_lec_context:
                lines.extend(["", lightweight_lec_context])
        if request in {Request.TALK, Request.VOTE}:
            vote_declaration_context = self._build_vote_declaration_context(info)
            if vote_declaration_context:
                lines.extend(["", vote_declaration_context])
        if request == Request.TALK:
            direct_question_context = self._build_direct_question_context(info)
            if direct_question_context:
                lines.extend(["", direct_question_context])
            own_black_context = self._build_own_black_context(info)
            if own_black_context:
                lines.extend(["", own_black_context])
            suspicion_context = self._build_suspicion_context(info)
            if suspicion_context:
                lines.extend(["", suspicion_context])
        return lines

    def _build_game_context(self, request: Request) -> str:
        """Build an explicit, organized context block for the current LLM call."""
        if self.info is None:
            return ""

        lines = self._build_game_context_header(request, self.info)
        lines += [
            "",
            "[Today's conversation history]",
            self._format_history(self._current_day_history(self.talk_history)),
            "",
            "[Full conversation history]",
            self._format_history(self.talk_history),
        ]
        if self.whisper_history:
            lines += ["", "[Whisper history]", self._format_history(self.whisper_history)]
        return "\n".join(lines)

    def _get_prompt_fragment(self, key: str) -> str:
        """Return a reusable prompt fragment from config.prompt."""
        prompt_config = self._as_dict(self.config.get("prompt"))
        value = prompt_config.get(key)
        return value if isinstance(value, str) else ""

    def _experiment_config(self) -> dict[str, Any]:
        """Return optional experiment controls without changing production defaults."""
        return self._as_dict(self.config.get("experiment"))

    def _dynamic_context_enabled(self) -> bool:
        """Return whether derived, time-varying context inserts are enabled."""
        return bool(self._experiment_config().get("dynamic_context_enabled", True))

    def _pp_instruction_enabled(self) -> bool:
        """Return whether PP-specific instructions may be inserted.

        The production default stays enabled.  P-PP configs explicitly disable
        only PP guidance while retaining the rest of the dynamic context.
        """
        return bool(self._experiment_config().get("pp_instruction_enabled", True))

    def _get_role_strategy(self) -> str:
        """Return configured strategy text for the agent role."""
        experiment_config = self._experiment_config()
        if not bool(experiment_config.get("role_strategy_enabled", True)):
            generic_strategy = experiment_config.get("generic_role_strategy", "")
            return generic_strategy if isinstance(generic_strategy, str) else ""

        strategy_config = self._as_dict(self.config.get("strategy"))
        if not strategy_config:
            return ""

        role_value = self._value_text(self.role)
        role_name = getattr(self.role, "name", "")
        aliases = {
            "占い師": "SEER",
            "人狼": "WEREWOLF",
            "狂人": "POSSESSED",
            "村人": "VILLAGER",
            "霊媒師": "MEDIUM",
            "霊能者": "MEDIUM",
            "騎士": "BODYGUARD",
            "狩人": "BODYGUARD",
        }
        candidates = [
            role_value,
            role_name,
            role_value.upper(),
            aliases.get(role_value, ""),
        ]
        for candidate in candidates:
            value = strategy_config.get(candidate)
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _as_dict(value: object) -> dict[str, Any]:
        """Return value as a dictionary when possible.

        Args:
            value (object): Value to inspect.

        Returns:
            dict[str, Any]: Dictionary value or an empty dictionary.
        """
        if not isinstance(value, dict):
            return {}
        raw_value = cast("dict[object, object]", value)
        return {str(key): dict_value for key, dict_value in raw_value.items()}

    @staticmethod
    def _as_list(value: object) -> list[Any]:
        """Return value as a list-like object.

        Args:
            value (object): Value to inspect.

        Returns:
            list[Any]: Normalized list.
        """
        if isinstance(value, list):
            return cast("list[Any]", value)
        if value is None:
            return []
        return [value]

    @staticmethod
    def _cast_profile_value(value: str, value_type: str) -> object:
        """Cast an extracted profile value.

        Args:
            value (str): Extracted text.
            value_type (str): Configured type name.

        Returns:
            object: Cast value.
        """
        if value_type == "int":
            with contextlib.suppress(ValueError):
                return int(value)
        return value.strip()

    def _get_speech_style_config(self) -> dict[str, Any]:
        """Return speech style configuration.

        Returns:
            dict[str, Any]: Speech style config.
        """
        return self._as_dict(self.config.get("speech_style"))

    def _uses_speech_style(self, request: Request | None) -> bool:
        """Return whether the request should receive speech style instructions.

        Args:
            request (Request | None): Current request.

        Returns:
            bool: True when speech style should be applied.
        """
        if request is None:
            return False

        speech_style_config = self._get_speech_style_config()
        if not bool(speech_style_config.get("enabled", False)):
            return False

        target_requests = {str(item).lower() for item in self._as_list(speech_style_config.get("target_requests"))}
        return str(request.lower()) in target_requests

    def _extract_profile_values(self, profile: str) -> dict[str, object]:
        """Extract configured values from profile text.

        Args:
            profile (str): Profile text.

        Returns:
            dict[str, object]: Extracted profile values.
        """
        speech_style_config = self._get_speech_style_config()
        extractors = self._as_dict(speech_style_config.get("profile_extractors"))
        values: dict[str, object] = {}
        for name, extractor in extractors.items():
            extractor_config = self._as_dict(extractor)
            pattern = extractor if isinstance(extractor, str) else extractor_config.get("pattern")
            if not isinstance(pattern, str):
                continue
            match = re.search(pattern, profile)
            if match:
                values[str(name)] = self._cast_profile_value(match.group(1), str(extractor_config.get("type", "str")))
        return values

    @staticmethod
    def _matches_profile_condition(profile: str, profile_values: dict[str, object], condition: dict[str, Any]) -> bool:
        """Return whether a configured rule condition matches.

        Args:
            profile (str): Profile text.
            profile_values (dict[str, object]): Extracted profile values.
            condition (dict[str, Any]): Rule condition.

        Returns:
            bool: True when all configured predicates match.
        """
        keywords = [str(keyword) for keyword in Agent._as_list(condition.get("keywords"))]
        any_keywords = [str(keyword) for keyword in Agent._as_list(condition.get("any_keywords"))]
        exclude_keywords = [str(keyword) for keyword in Agent._as_list(condition.get("exclude_keywords"))]
        if any(keyword not in profile for keyword in keywords):
            return False
        if any_keywords and not any(keyword in profile for keyword in any_keywords):
            return False
        if any(keyword in profile for keyword in exclude_keywords):
            return False

        age = profile_values.get("age")
        min_age = condition.get("min_age")
        max_age = condition.get("max_age")
        if isinstance(age, int) and isinstance(min_age, int | float) and age < min_age:
            return False
        if isinstance(age, int) and isinstance(max_age, int | float) and age > max_age:
            return False

        reserved_keys = {"keywords", "any_keywords", "exclude_keywords", "min_age", "max_age"}
        return all(profile_values.get(key) == value for key, value in condition.items() if key not in reserved_keys)

    def _select_unique_first_person(self, speech_style_config: dict[str, Any]) -> str | None:
        """Select a deterministic first-person pronoun for this game.

        Args:
            speech_style_config (dict[str, Any]): Speech style config.

        Returns:
            str | None: Selected first-person pronoun when configured.
        """
        unique_config = self._as_dict(speech_style_config.get("unique_first_person"))
        if not bool(unique_config.get("enabled", False)):
            return None

        candidates = [str(candidate) for candidate in self._as_list(unique_config.get("candidates")) if candidate]
        if self.info is None or not candidates:
            return None

        agent_names = sorted(self.info.status_map)
        if self.info.agent not in agent_names:
            return None

        overrides = self._as_dict(unique_config.get("overrides"))
        assigned: dict[str, str] = {}
        for agent_name in agent_names:
            override = str(overrides[agent_name]) if agent_name in overrides else None
            if override and override not in assigned.values():
                assigned[agent_name] = override
            else:
                assigned[agent_name] = next(
                    (candidate for candidate in candidates if candidate not in assigned.values()),
                    candidates[len(assigned) % len(candidates)],
                )
            if agent_name == self.info.agent:
                return assigned[agent_name]
        return None

    def _build_speech_style_instruction(self) -> str:
        """Build a speech-style instruction from configuration.

        Returns:
            str: Prompt fragment for configured requests.
        """
        profile = self.info.profile if self.info is not None and self.info.profile is not None else self.profile
        if profile is None:
            return ""

        speech_style_config = self._get_speech_style_config()
        if not bool(speech_style_config.get("enabled", False)):
            return ""

        profile_values = self._extract_profile_values(profile)
        style = self._as_dict(speech_style_config.get("defaults")).copy()
        for rule in self._as_list(speech_style_config.get("rules")):
            rule_config = self._as_dict(rule)
            condition = self._as_dict(rule_config.get("when"))
            if self._matches_profile_condition(profile, profile_values, condition):
                style.update(self._as_dict(rule_config.get("set")))
        unique_first_person = self._select_unique_first_person(speech_style_config)
        if unique_first_person is not None:
            style["first_person"] = unique_first_person

        template_text = speech_style_config.get("template")
        if not isinstance(template_text, str):
            return ""
        return Template(template_text).render(
            info=self.info,
            profile=profile,
            profile_values=profile_values,
            style=style,
        )

    def _has_mention(self, text: str) -> bool:
        """Return whether text mentions another player.

        Args:
            text (str): Sanitized response text / サニタイズ済みレスポンス

        Returns:
            bool: True if the text contains a player mention / プレイヤー名への言及がある場合True
        """
        return self._mention_match(text) is not None

    def _mention_match(self, text: str) -> re.Match[str] | None:
        """Return the first valid @name mention match."""
        if not self.info or "@" not in text:
            return None
        other_agents = [
            re.escape(agent_name)
            for agent_name in self.info.status_map
            if agent_name != self.agent_name
        ]
        if not other_agents:
            return None
        names_pattern = "|".join(sorted(other_agents, key=len, reverse=True))
        return re.search(rf"@\s*({names_pattern})", text)

    def _freeform_text_length_limits(self, request: Request | None = None) -> tuple[int, int]:
        """Return base and mention text limits from server settings."""
        phase_setting: Any | None = None
        if self.setting is not None:
            active_request = request or self.request
            phase_setting = self.setting.whisper if active_request == Request.WHISPER else self.setting.talk
        max_length = getattr(phase_setting, "max_length", None)
        base_length = getattr(max_length, "base_length", None) or BASE_FREEFORM_TEXT_LENGTH
        mention_length = getattr(max_length, "mention_length", None) or MENTION_FREEFORM_TEXT_LENGTH
        return int(base_length), int(mention_length)

    def _freeform_text_length_limit(self, text: str, request: Request | None = None) -> int:
        """Return the server text limit for a freeform response.

        Args:
            text (str): Sanitized response text / サニタイズ済みレスポンス

        Returns:
            int: Character limit for the response / 発話の文字数上限
        """
        base_length, mention_length = self._freeform_text_length_limits(request)
        if self._has_mention(text):
            return base_length + mention_length
        return base_length

    @staticmethod
    def _count_non_space_characters(text: str) -> int:
        """Count characters using the contest server's space-excluding rule."""
        return sum(not character.isspace() for character in text)

    def _truncate_freeform_segment(self, text: str, length_limit: int) -> str:
        """Truncate a segment by non-space characters, preserving a useful boundary."""
        if length_limit <= 0:
            return ""
        if self._count_non_space_characters(text) <= length_limit:
            return text

        characters: list[str] = []
        counted = 0
        for character in text:
            if not character.isspace():
                counted += 1
            if counted > length_limit:
                break
            characters.append(character)
        truncated = "".join(characters).rstrip()

        min_truncate_length = int(length_limit * MIN_SENTENCE_TRUNCATE_RATIO)
        sentence_end = max(truncated.rfind(ending) for ending in SENTENCE_ENDINGS)
        if sentence_end >= 0:
            sentence = truncated[: sentence_end + 1].strip()
            if self._count_non_space_characters(sentence) >= min_truncate_length:
                return sentence

        soft_break = max(truncated.rfind(break_char) for break_char in SOFT_BREAKS)
        if soft_break >= 0:
            candidate = truncated[:soft_break].rstrip()
            if self._count_non_space_characters(candidate) >= min_truncate_length:
                truncated = candidate

        if truncated.endswith(SENTENCE_ENDINGS):
            return truncated
        while truncated and self._count_non_space_characters(truncated) >= length_limit:
            truncated = truncated[:-1].rstrip()
        return f"{truncated}." if truncated else ""

    def _sanitize_mentioned_freeform_text(
        self,
        text: str,
        mention_match: re.Match[str],
        request: Request | None = None,
    ) -> str:
        """Sanitize text with a valid mention using separate base and mention limits."""
        base_length, mention_length = self._freeform_text_length_limits(request)
        prefix = self._truncate_freeform_segment(text[: mention_match.start()].rstrip(), base_length)
        mention = f"@{mention_match.group(1)}"
        suffix = self._truncate_freeform_segment(text[mention_match.end() :].lstrip(), mention_length)

        parts = [part for part in (prefix, mention, suffix) if part]
        return " ".join(parts)

    def _guard_language(self) -> str:
        """Return the configured contest language for the output guard."""
        agent_config = self._as_dict(self.config.get("agent"))
        guard_config = self._as_dict(agent_config.get("guard"))
        return str(guard_config.get("language", "en")).lower()

    def _normalize_player_mentions(self, text: str) -> str:
        """Keep only mentions whose names were supplied by the game server."""
        valid_names: set[str] = set(self.info.status_map) if self.info else set()

        def replace_mention(match: re.Match[str]) -> str:
            name = match.group(1)
            return f"@{name}" if name in valid_names else ""

        return INVALID_MENTION_PATTERN.sub(replace_mention, text)

    @staticmethod
    def _contains_non_english_letters(text: str) -> bool:
        """Return whether text contains letters outside English ASCII."""
        return any(character.isalpha() and not character.isascii() for character in text)

    @staticmethod
    def _looks_like_protocol_output(text: str) -> bool:
        """Return whether text is an AIWolf protocol command rather than dialog."""
        return PROTOCOL_OUTPUT_PATTERN.fullmatch(text) is not None

    def _freeform_length_exceeded(self, text: str, request: Request | None = None) -> bool:
        """Return whether text exceeds the server-provided per-segment limits."""
        base_length, mention_length = self._freeform_text_length_limits(request)
        mention_match = self._mention_match(text)
        if mention_match is None:
            return self._count_non_space_characters(text) > base_length
        before = text[: mention_match.start()]
        after = text[mention_match.end() :]
        return (
            self._count_non_space_characters(before) > base_length
            or self._count_non_space_characters(after) > mention_length
        )

    @staticmethod
    def _looks_like_leaked_work_note(text: str) -> bool:
        """Return whether a freeform response appears to contain LLM work notes."""
        stripped = text.strip()
        if stripped in {"Skip", "Over"}:
            return False
        if LEAKED_WORK_NOTE_PATTERN.search(stripped):
            return True
        if not JAPANESE_TEXT_PATTERN.search(stripped):
            return False

        ascii_words = [
            word.lower()
            for word in ASCII_WORD_PATTERN.findall(stripped)
            if word.lower() not in ALLOWED_FREEFORM_ASCII_WORDS
        ]
        return len(ascii_words) >= MIN_SUSPICIOUS_ASCII_WORDS

    @staticmethod
    def _remove_json_literals(text: str) -> str:
        """Remove JSON literals accidentally included in a freeform response."""
        decoder = json.JSONDecoder()
        output: list[str] = []
        index = 0
        while index < len(text):
            if text[index] in "{[":
                with contextlib.suppress(json.JSONDecodeError):
                    _, end = decoder.raw_decode(text[index:])
                    index += end
                    continue
            output.append(text[index])
            index += 1
        return "".join(output)

    @classmethod
    def _extract_freeform_text(cls, text: str) -> str:
        """Extract the actual utterance if the model leaks internal JSON."""
        stripped = text.strip()
        with contextlib.suppress(json.JSONDecodeError):
            parsed: object = json.loads(stripped)
            if isinstance(parsed, dict):
                parsed_dict = cast("dict[str, object]", parsed)
                for key in FREEFORM_TEXT_JSON_KEYS:
                    value = parsed_dict.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                if "intent_type" in parsed_dict:
                    return "Skip"

        without_fences = FENCED_CODE_BLOCK_PATTERN.sub("", stripped)
        without_json = cls._remove_json_literals(without_fences)
        lines = [
            FREEFORM_OUTPUT_LABEL_PATTERN.sub("", line).strip()
            for line in without_json.splitlines()
        ]
        extracted = "\n".join(line for line in lines if line).strip()
        return extracted or "Skip"

    def _sanitize_freeform_text(self, text: str, request: Request | None = None) -> str:
        """Make freeform talk/whisper text safe for the realtime server limits.

        Args:
            text (str): Raw LLM response / LLMの生レスポンス

        Returns:
            str: Text within the server's freeform constraints / 制約内の発話
        """
        sanitized = self._extract_freeform_text(text)
        sanitized = self._normalize_player_mentions(sanitized)
        if self._looks_like_protocol_output(sanitized):
            self.agent_logger.logger.warning(
                "Skipping protocol-like output in a natural-language phase: %s",
                sanitized,
            )
            return "Skip"
        if self._guard_language() == "en":
            sanitized = sanitized.translate(ENGLISH_PUNCTUATION_TRANSLATION)
            if self._contains_non_english_letters(sanitized):
                self.agent_logger.logger.warning(
                    "Skipping a non-English utterance: %s",
                    sanitized,
                )
                return "Skip"
            sanitized = sanitized.replace(",", ".")
            sanitized = ENGLISH_ALLOWED_TEXT_PATTERN.sub("", sanitized)
        else:
            sanitized = JAPANESE_ALLOWED_TEXT_PATTERN.sub("", sanitized)
            sanitized = sanitized.replace(",", "、")
        sanitized = " ".join(sanitized.split()).strip()
        if self._looks_like_leaked_work_note(sanitized):
            self.agent_logger.logger.warning(
                "Skipping utterance suspected of containing work notes: %s",
                sanitized,
            )
            return "Skip"
        mention_match = self._mention_match(sanitized)
        if mention_match is not None:
            return self._sanitize_mentioned_freeform_text(sanitized, mention_match, request)

        length_limit = self._freeform_text_length_limit(sanitized, request)
        return self._truncate_freeform_segment(sanitized, length_limit)

    @timeout
    def name(self) -> str:
        """Return response to name request.

        名前リクエストに対する応答を返す.

        Returns:
            str: Agent name / エージェント名
        """
        return self.agent_name

    def initialize(self) -> None:
        """Perform initialization for game start request.

        ゲーム開始リクエストに対する初期化処理を行う.
        """
        if self.info is None:
            return

        model_type = str(self.config["llm"]["type"])
        match model_type:
            case "openai":
                configured_temperature = float(self.config["openai"]["temperature"])
                transport = {}
                if self.config['openai'].get('request_timeout_seconds'):
                    import httpx
                    transport['timeout'] = httpx.Timeout(
                        float(self.config['openai']['request_timeout_seconds']),
                        connect=float(self.config['openai'].get('connect_timeout_seconds', 5)),
                    )
                openai_model = ChatOpenAI(
                    model=str(self.config["openai"]["model"]),
                    temperature=configured_temperature,
                    max_retries=int(self.config["openai"].get("max_retries", 0)),
                    api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
                    **transport,
                )
                effective_temperature = openai_model.temperature
                if effective_temperature != configured_temperature:
                    mismatch = (
                        "OpenAI temperature was changed by the SDK: "
                        f"configured={configured_temperature}, effective={effective_temperature}"
                    )
                    raise ValueError(mismatch)
                self.llm_model = openai_model
            case "google":
                self.llm_model = ChatGoogleGenerativeAI(
                    model=str(self.config["google"]["model"]),
                    temperature=float(self.config["google"]["temperature"]),
                    api_key=SecretStr(os.environ["GOOGLE_API_KEY"]),
                )
            case "ollama":
                self.llm_model = ChatOllama(
                    model=str(self.config["ollama"]["model"]),
                    temperature=float(self.config["ollama"]["temperature"]),
                    base_url=str(self.config["ollama"]["base_url"]),
                )
            case _:
                raise ValueError(model_type, "Unknown LLM type")

    def daily_initialize(self) -> None:
        """Perform processing for daily initialization request.

        昼開始リクエストに対する処理を行う.
        """
        return

    def whisper(self) -> str:
        """Return response to whisper request.

        囁きリクエストに対する応答を返す.

        Returns:
            str: Whisper message / 囁きメッセージ
        """
        response = self._send_message_to_llm(Request.WHISPER)
        self.sent_whisper_count = len(self.whisper_history)
        return self._process_output_guard(Request.WHISPER, response)

    def talk(self) -> str:
        """Return response to talk request.

        トークリクエストに対する応答を返す.

        Returns:
            str: Talk message / 発言メッセージ
        """
        if self._should_skip_day0_after_greeting():
            self.agent_logger.logger.info("Day 0 greeting already sent; returning Skip without LLM call.")
            self.sent_talk_count = len(self.talk_history)
            return "Skip"
        response = self._send_message_to_llm(Request.TALK)
        self.sent_talk_count = len(self.talk_history)
        return self._process_output_guard(Request.TALK, response)

    def daily_finish(self) -> None:
        """Perform processing for daily finish request.

        昼終了リクエストに対する処理を行う.
        """
        return

    def _extract_action_target(self, response: str | None) -> str:
        """Extract a valid alive agent name from the response or return a fallback.

        Args:
            response (str | None): LLM response / LLMのレスポンス

        Returns:
            str: Valid agent name / 有効なエージェント名
        """
        alive_agents = self.get_alive_agents()
        valid_targets = [agent for agent in alive_agents if self.info and agent != self.info.agent]
        if not valid_targets:
            # Fallback if no valid targets
            valid_targets = alive_agents

        if response:
            for agent in valid_targets:
                if agent in response:
                    return agent

        # Fallback to random if no name found
        return random.choice(valid_targets) if valid_targets else ""  # noqa: S311

    def _likely_possessed_attack_targets(self) -> set[str]:
        """Return seer claimants that should be preserved as likely possessed allies."""
        seer_claimants = self._extract_seer_claims()
        if len(seer_claimants) < MIN_COMPETING_SEER_CLAIMS or self.info is None:
            return set()

        claims = self._extract_divination_claims(seer_claimants, list(self.info.status_map))
        likely_possessed: set[str] = set()
        for reporter, judged in claims.items():
            for target, result in judged.items():
                if result == "white" and target in seer_claimants and target != reporter:
                    likely_possessed.add(target)
        return likely_possessed

    def _safe_attack_target(self, target: str) -> str:
        """Avoid attacks that remove likely possessed allies when alternatives exist."""
        if self.role != Role.WEREWOLF or self.info is None or not target:
            return target

        likely_possessed = self._likely_possessed_attack_targets()
        if target not in likely_possessed:
            return target

        alive_agents = self.get_alive_agents()
        valid_targets = [agent for agent in alive_agents if agent != self.info.agent]
        alternatives = [agent for agent in valid_targets if agent not in likely_possessed]
        if not alternatives:
            return target

        seer_claimants = self._extract_seer_claims()
        preferred = [
            agent
            for agent in alternatives
            if agent in seer_claimants
        ]
        replacement = preferred[0] if preferred else alternatives[0]
        self.agent_logger.logger.warning(
            "Avoiding attack on a likely possessed ally: %s -> %s",
            target,
            replacement,
        )
        return replacement

    def divine(self) -> str:
        """Return response to divine request.

        占いリクエストに対する応答を返す.

        Returns:
            str: Agent name to divine / 占い対象のエージェント名
        """
        response = self._send_message_to_llm(Request.DIVINE)
        return self._process_output_guard(Request.DIVINE, response)

    def guard(self) -> str:
        """Return response to guard request.

        護衛リクエストに対する応答を返す.

        Returns:
            str: Agent name to guard / 護衛対象のエージェント名
        """
        response = self._send_message_to_llm(Request.GUARD)
        return self._process_output_guard(Request.GUARD, response)

    def vote(self) -> str:
        """Return response to vote request.

        投票リクエストに対する応答を返す.

        Returns:
            str: Agent name to vote / 投票対象のエージェント名
        """
        response = self._send_message_to_llm(Request.VOTE)
        return self._process_output_guard(Request.VOTE, response)

    def attack(self) -> str:
        """Return response to attack request.

        襲撃リクエストに対する応答を返す.

        Returns:
            str: Agent name to attack / 襲撃対象のエージェント名
        """
        response = self._send_message_to_llm(Request.ATTACK)
        return self._process_output_guard(Request.ATTACK, response)

    def _extract_mentioned_player_names(self, text: str) -> set[str]:
        """Extract all player-name-like tokens mentioned in a freeform text.

        Uses only the current game's status_map as the reference.  The method
        looks for three patterns:
          - @Name mentions (e.g. "@ミナト")
          - "Nameさん" suffixed references (e.g. "ミナトさん")
          - Bare occurrences of any known game-participant name

        Args:
            text (str): Freeform text to scan.

        Returns:
            set[str]: Set of matched tokens (raw strings, not validated yet).
        """
        if not self.info:
            return set()

        # Build a pattern that matches any participant name found in status_map.
        all_game_names = sorted(self.info.status_map.keys(), key=len, reverse=True)
        if not all_game_names:
            return set()

        found: set[str] = set()

        # Pattern 1: @Name
        for m in re.finditer(r"@\s*([\w\u3041-\u30FA\u4E00-\u9FFF\u3005\u3007]+)", text):
            found.add(m.group(1).strip())

        # Pattern 2: Nameさん
        for m in re.finditer(r"([\w\u3041-\u30FA\u4E00-\u9FFF\u3005\u3007]+)さん", text):
            found.add(m.group(1).strip())

        # Pattern 3: bare occurrence of any participant name
        name_pattern = re.compile(
            "|".join(re.escape(name) for name in all_game_names),
        )
        for m in name_pattern.finditer(text):
            found.add(m.group(0))

        return found

    def _detect_violations(self, request: Request, raw_content: str) -> list[str]:
        """Inspect LLM output for rule violations before sending to the server.

        Rules checked for TALK / WHISPER:
          - LENGTH_EXCEEDED   : exceeds a server-provided segment limit
          - HALF_WIDTH_COMMA  : contains a forbidden half-width comma
          - NON_ENGLISH_TEXT  : contains non-English letters in the English track
          - PROTOCOL_OUTPUT   : contains a command instead of natural-language dialog
          - INVALID_PLAYER_MENTION : mentions a name that is not a game participant

        Rules checked for action requests (VOTE / DIVINE / GUARD / ATTACK):
          - NO_TARGET_FOUND   : no participant name could be extracted
          - INVALID_PLAYER_NAME : extracted name is not a game participant
          - DEAD_TARGET       : target player is not ALIVE
          - SELF_VOTE         : agent votes for itself (VOTE only)

        Args:
            request (Request): The request type being processed.
            raw_content (str): Extracted / stripped LLM output text.

        Returns:
            list[str]: Violation codes found (may be empty).
        """
        violations: list[str] = []
        if not self.info:
            return violations

        valid_names: set[str] = set(self.info.status_map.keys())

        if request in (Request.TALK, Request.WHISPER):
            if self._freeform_length_exceeded(raw_content, request):
                violations.append("LENGTH_EXCEEDED")
            if "," in raw_content:
                violations.append("HALF_WIDTH_COMMA")
            if self._guard_language() == "en" and self._contains_non_english_letters(raw_content):
                violations.append("NON_ENGLISH_TEXT")
            if self._looks_like_protocol_output(raw_content):
                violations.append("PROTOCOL_OUTPUT")

            # Rule: only real player names
            mentioned = self._extract_mentioned_player_names(raw_content)
            invalid = mentioned - valid_names
            if invalid:
                violations.append("INVALID_PLAYER_MENTION")
                self.agent_logger.logger.warning(
                    "Output guard: response contains a non-existent player name: %s",
                    invalid,
                )
        else:
            # Action requests: VOTE, DIVINE, GUARD, ATTACK
            # Extract the intended target from the response using participant names only.
            target: str | None = None
            for name in sorted(valid_names, key=len, reverse=True):
                if name in raw_content:
                    target = name
                    break

            if target is None:
                violations.append("NO_TARGET_FOUND")
            else:
                # Rule: only real player names (already guaranteed by extraction above)

                # Rule: only living players may be targeted
                if self.info.status_map.get(target) != Status.ALIVE:
                    violations.append("DEAD_TARGET")
                    self.agent_logger.logger.warning(
                        "Output guard: a dead player was selected as target: %s",
                        target,
                    )

                allow_self_vote = bool(
                    self.setting
                    and request == Request.VOTE
                    and getattr(self.setting.vote, "allow_self_vote", False)
                )
                if request == Request.VOTE and target == self.info.agent and not allow_self_vote:
                    violations.append("SELF_VOTE")
                    self.agent_logger.logger.warning(
                        "Output guard: a self-vote was detected: %s",
                        target,
                    )

        return violations

    def _apply_freeform_guard(self, request: Request, raw_response: str) -> str:
        """Apply output guard corrections to a freeform (TALK/WHISPER) response.

        This delegates to the standard sanitizer so guard-enabled and normal
        output paths always enforce the same tournament constraints.

        Args:
            raw_response (str): Raw LLM response for a freeform request.

        Returns:
            str: Corrected freeform text, or 'Skip' if nothing remains.
        """
        return self._sanitize_freeform_text(raw_response, request) or "Skip"

    def _apply_guard(self, request: Request, raw_response: str, violations: list[str]) -> str:
        """Return the corrected response after applying output guard rules.

        When there are no violations the standard sanitization path is used.
        When violations exist the appropriate correction strategy is applied:
          - TALK / WHISPER: attempt to fix and return corrected text.
          - Action requests: fall back to a safe random alive target.

        Args:
            request (Request): Request type.
            raw_response (str): Raw LLM response.
            violations (list[str]): Violation codes detected by _detect_violations.

        Returns:
            str: Safe response to send to the server.
        """
        if not violations:
            # No violations: use standard sanitization
            if request in (Request.TALK, Request.WHISPER):
                return self._sanitize_freeform_text(raw_response, request)
            target = self._extract_action_target(raw_response)
            if request == Request.ATTACK:
                target = self._safe_attack_target(target)
            return target

        if request in (Request.TALK, Request.WHISPER):
            # Attempt to fix freeform violations in-place
            return self._apply_freeform_guard(request, raw_response)

        # Action request with violations: fall back to a safe alive target
        self.agent_logger.logger.warning(
            "Output guard: using a fallback due to action violations %s (request=%s)",
            violations,
            request,
        )
        fallback = self._fallback_action_target()
        if request == Request.ATTACK and fallback:
            fallback = self._safe_attack_target(fallback)
        return fallback if fallback else ""

    def _log_violations(
        self,
        request: Request,
        raw_content: str,
        final_response: str,
        violations: list[str],
        guard_enabled: bool,
    ) -> None:
        """Append an output-audit record to the per-game JSONL log file.

        Args:
            request (Request): Request type.
            raw_content (str): Extracted / stripped LLM output before correction.
            final_response (str): Final response sent to the server.
            violations (list[str]): Violation codes detected.
            guard_enabled (bool): Whether the output guard was active.
        """
        guard_config = self._as_dict(self._as_dict(self.config.get("agent")).get("guard"))
        if not violations and not bool(guard_config.get("audit_all_outputs", False)):
            # Avoid per-response disk I/O on the normal, compliant path.
            return
        with contextlib.suppress(Exception):
            self.agent_logger.output_dir.mkdir(parents=True, exist_ok=True)
            violations_path = self.agent_logger.output_dir / "rule_violations.jsonl"
            record = {
                "timestamp": datetime.now(UTC).astimezone().isoformat(),
                "game_id": self._value_text(getattr(self.info, "game_id", "")),
                "day": getattr(self.info, "day", None),
                "agent_name": self.agent_name,
                "role": self._value_text(self.role),
                "request": self._value_text(request),
                "raw_llm_response": raw_content,
                "final_response": final_response,
                "violations": violations,
                "guard_enabled": guard_enabled,
                "modified": raw_content != final_response,
                "guarded": guard_enabled and len(violations) > 0,
                "condition": str(self._experiment_config().get("condition", "")),
            }
            with violations_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _process_output_guard(self, request: Request, raw_response: str | None) -> str:
        """Run the full output guard pipeline for one LLM response.

        Pipeline:
          1. Extract the intended text / target from the raw response.
          2. Detect rule violations.
          3. Apply corrections when guard is enabled. When disabled, use the
             configured raw pass-through or the backward-compatible sanitizer.
          4. Log detected violations, or every response when audit_all_outputs
             is enabled.

        Args:
            request (Request): Request type being processed.
            raw_response (str | None): Raw LLM response text.

        Returns:
            str: Safe, server-ready response string.
        """
        if raw_response is None:
            raw_response = ""

        # Step 1: extract the intended content
        if request in (Request.TALK, Request.WHISPER):
            raw_content = self._extract_freeform_text(raw_response)
        else:
            raw_content = raw_response.strip()

        # Step 2: detect violations
        violations = self._detect_violations(request, raw_content)

        if violations:
            self.agent_logger.logger.warning(
                "Output guard: violations detected %s (request=%s)",
                violations,
                request,
            )

        # Step 3: determine final response
        guard_enabled: bool = False
        with contextlib.suppress(Exception):
            guard_enabled = bool(
                self.config.get("agent", {}).get("guard", {}).get("enabled", False),
            )

        if guard_enabled:
            final_response = self._apply_guard(request, raw_response, violations)
        else:
            guard_config = self._as_dict(self._as_dict(self.config.get("agent")).get("guard"))
            if guard_config.get("disabled_behavior", "sanitize") == "raw":
                # Experimental G condition: preserve the model output so that
                # server-visible failures remain measurable. Detection and
                # logging above stay active but do not modify the response.
                final_response = raw_response
            elif request in (Request.TALK, Request.WHISPER):
                final_response = self._sanitize_freeform_text(raw_response, request)
            else:
                final_response = self._extract_action_target(raw_response)
                if request == Request.ATTACK:
                    final_response = self._safe_attack_target(final_response)

        # Step 4: log violations
        self._log_violations(request, raw_response, final_response, violations, guard_enabled)

        return final_response

    def finish(self) -> None:
        """Perform processing for game finish request.

        ゲーム終了リクエストに対する処理を行う.
        """

    @timeout
    def action(self) -> str | None:  # noqa: C901, PLR0911
        """Execute action according to request type.

        リクエストの種類に応じたアクションを実行する.

        Returns:
            str | None: Action result string or None / アクションの結果文字列またはNone
        """
        match self.request:
            case Request.NAME:
                return self.name()
            case Request.TALK:
                return self.talk()
            case Request.WHISPER:
                return self.whisper()
            case Request.VOTE:
                return self.vote()
            case Request.DIVINE:
                return self.divine()
            case Request.GUARD:
                return self.guard()
            case Request.ATTACK:
                return self.attack()
            case Request.INITIALIZE:
                self.initialize()
            case Request.DAILY_INITIALIZE:
                self.daily_initialize()
            case Request.DAILY_FINISH:
                self.daily_finish()
            case Request.FINISH:
                self.finish()
            case _:
                pass
        return None
