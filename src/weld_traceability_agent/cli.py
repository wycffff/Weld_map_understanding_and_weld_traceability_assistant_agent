from __future__ import annotations

import argparse
import json
from pathlib import Path

from weld_traceability_agent.config import load_agent_config
from weld_traceability_agent.orchestrator import AgentOrchestrator
from weld_traceability_agent.runtime_models import BotAttachment, IncomingMessage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weld traceability agent")
    parser.add_argument("--config", default="config/agent_config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_cmd = subparsers.add_parser("process-message")
    process_cmd.add_argument("--chat-id", required=True)
    process_cmd.add_argument("--user-id", required=True)
    process_cmd.add_argument("--message-id", required=True)
    process_cmd.add_argument("--text", default="")
    process_cmd.add_argument("--attachment")
    process_cmd.add_argument("--attachment-kind", default="image")

    subparsers.add_parser("run-telegram")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_agent_config(args.config)

    if args.command == "run-telegram":
        from weld_traceability_agent.telegram.bot import run_telegram_bot

        run_telegram_bot(config)
        return

    orchestrator = AgentOrchestrator(config)
    attachments: list[BotAttachment] = []
    if args.attachment:
        attachment_path = Path(args.attachment).resolve()
        attachments.append(
            BotAttachment(
                attachment_id="cli_attachment_1",
                kind=args.attachment_kind,
                file_name=attachment_path.name,
                local_path=str(attachment_path),
            )
        )
    incoming = IncomingMessage(
        channel="telegram",
        chat_id=str(args.chat_id),
        user_id=str(args.user_id),
        message_id=str(args.message_id),
        text=args.text,
        attachments=attachments,
    )
    plan = orchestrator.process_message(incoming)
    print(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
