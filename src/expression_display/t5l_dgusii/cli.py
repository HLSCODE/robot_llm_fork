from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace

import serial
from serial.tools import list_ports

from .client import DgusClient, print_trace
from .config import DgusSdkConfig, ExpressionConfig, load_config
from .controls import AnimationIconConfig, AnimationIconControl
from .sdk import create_sdk
from .services import Expression, ExpressionSwitcher, default_expressions, parse_expression_specs
from .utils import parse_int


DEFAULT_TX_DELAY = 0.05


def parse_addr_list(text: str) -> list[int]:
    return [parse_int(item) for item in text.split(",") if item.strip()]


def print_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("没有找到可用串口")
        return

    print("可用串口：")
    for port in ports:
        print(f"  {port.device:<8} {port.description}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T5L DGUSII serial SDK CLI")
    parser.add_argument("--config", help="JSON 配置文件路径，例如 t5l_config.json")
    parser.add_argument("--list-ports", action="store_true", help="列出可用串口")
    parser.add_argument("-p", "--port", help="串口号，例如 COM4")
    parser.add_argument("-b", "--baudrate", type=int, help="波特率")
    parser.add_argument("--timeout", type=float, help="串口读超时")
    parser.add_argument("--write-timeout", type=float, help="串口写超时")
    parser.add_argument("--tx-delay", type=float, help="每条发送后的延时")

    parser.add_argument("--vp", help="动画图标 VP 地址")
    parser.add_argument("--sp", help="动画图标 SP 描述指针地址")
    parser.add_argument("--start-value", help="动画开始值")
    parser.add_argument("--stop-value", help="动画停止值")
    parser.add_argument("--hide-value", help="动画不显示值")
    parser.add_argument(
        "--clear-before-switch",
        choices=["stop", "hide", "none"],
        help="切换前处理方式",
    )
    parser.add_argument("--switch-delay", type=float, help="切换步骤间延时")
    parser.add_argument("--no-update-range", action="store_true", help="切换时不写 ICON_Start/End")
    parser.add_argument("--clear-vps", help="切换前额外隐藏的旧 VP，逗号分隔")
    parser.add_argument(
        "--expressions",
        help="表情配置：name:lib:start:end,name:lib:start:end",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("console", help="进入交互式控制台")
    subparsers.add_parser("list", help="列出表情")

    switch_parser = subparsers.add_parser("switch", help="切换表情")
    switch_parser.add_argument("expression", help="表情名称或序号")

    lib_parser = subparsers.add_parser("lib", help="只修改 ICON_Lib")
    lib_parser.add_argument("icon_lib", help="图标库 ID，例如 24 或 0x18")

    word_parser = subparsers.add_parser("write-word", help="写一个 Word")
    word_parser.add_argument("addr")
    word_parser.add_argument("value")

    raw_parser = subparsers.add_parser("raw", help="写一个 Word，兼容旧脚本命令")
    raw_parser.add_argument("addr")
    raw_parser.add_argument("value")

    byte_parser = subparsers.add_parser("write-byte", help="写一个 Byte")
    byte_parser.add_argument("addr")
    byte_parser.add_argument("value")

    hide_vp_parser = subparsers.add_parser("hide-vp", help="写指定 VP 为 hide-value")
    hide_vp_parser.add_argument("addr")

    return parser


def _project_settings():
    from ..display import ExpressionDisplaySettings

    return ExpressionDisplaySettings.from_project_config()


def _config_from_project_settings(settings) -> DgusSdkConfig:
    return DgusSdkConfig(
        serial=replace(
            DgusSdkConfig().serial,
            port=settings.port,
            baudrate=settings.baudrate,
            timeout=settings.timeout,
            write_timeout=settings.write_timeout,
        ),
        animation_icon=AnimationIconConfig(
            vp_addr=settings.vp_addr,
            sp_addr=settings.sp_addr,
            start_value=settings.start_value,
            stop_value=settings.stop_value,
            hide_value=settings.hide_value,
            clear_before_switch=settings.clear_before_switch,
            switch_delay=settings.switch_delay,
            update_icon_range=settings.update_icon_range,
        ),
        expressions=[
            ExpressionConfig(item.name, item.icon_lib, item.icon_start, item.icon_end)
            for item in settings.expressions
        ],
        clear_vps=list(settings.clear_vps),
        test_interval=settings.test_interval,
    )


def resolve_config(args: argparse.Namespace) -> DgusSdkConfig:
    if args.config:
        args._resolved_tx_delay = args.tx_delay if args.tx_delay is not None else DEFAULT_TX_DELAY
        return load_config(args.config)

    settings = _project_settings()
    args._resolved_tx_delay = args.tx_delay if args.tx_delay is not None else settings.tx_delay
    if settings.config_path is not None:
        return load_config(settings.config_path)
    return _config_from_project_settings(settings)


def resolve_expressions(args: argparse.Namespace, config: DgusSdkConfig) -> list[Expression]:
    if args.expressions is not None:
        return parse_expression_specs(args.expressions)
    expressions = config.expression_models()
    return expressions or default_expressions()


def create_switcher(args: argparse.Namespace) -> tuple[DgusClient, AnimationIconControl, ExpressionSwitcher]:
    config = resolve_config(args)
    serial_config = config.serial
    animation_config = config.animation_icon

    config = DgusSdkConfig(
        serial=replace(
            serial_config,
            port=args.port or serial_config.port,
            baudrate=args.baudrate if args.baudrate is not None else serial_config.baudrate,
            timeout=args.timeout if args.timeout is not None else serial_config.timeout,
            write_timeout=(
                args.write_timeout
                if args.write_timeout is not None
                else serial_config.write_timeout
            ),
        ),
        animation_icon=replace(
            animation_config,
            vp_addr=parse_int(args.vp) if args.vp is not None else animation_config.vp_addr,
            sp_addr=parse_int(args.sp) if args.sp is not None else animation_config.sp_addr,
            start_value=(
                parse_int(args.start_value)
                if args.start_value is not None
                else animation_config.start_value
            ),
            stop_value=(
                parse_int(args.stop_value)
                if args.stop_value is not None
                else animation_config.stop_value
            ),
            hide_value=(
                parse_int(args.hide_value)
                if args.hide_value is not None
                else animation_config.hide_value
            ),
            clear_before_switch=args.clear_before_switch or animation_config.clear_before_switch,
            switch_delay=(
                args.switch_delay
                if args.switch_delay is not None
                else animation_config.switch_delay
            ),
            update_icon_range=False if args.no_update_range else animation_config.update_icon_range,
        ),
        expressions=config.expressions,
        clear_vps=parse_addr_list(args.clear_vps) if args.clear_vps is not None else config.clear_vps,
        test_interval=config.test_interval,
    )

    if args.expressions is not None:
        config = replace(
            config,
            expressions=[
                ExpressionConfig(item.name, item.icon_lib, item.icon_start, item.icon_end)
                for item in parse_expression_specs(args.expressions)
            ],
        )

    sdk = create_sdk(
        config_path=None,
        config=config,
        tx_delay=getattr(args, "_resolved_tx_delay", DEFAULT_TX_DELAY),
        trace=print_trace,
    )
    return sdk.client, sdk.animation_icon, sdk.expression_service


def show_expressions(expressions: Sequence[Expression], current: str | None = None) -> None:
    print("表情列表：")
    for index, expression in enumerate(expressions, start=1):
        mark = " <当前>" if current == expression.name else ""
        print(
            f"  {index}. {expression.name:<12} "
            f"ICON_Lib={expression.icon_lib:>3} / 0x{expression.icon_lib:02X}, "
            f"ICON_Start={expression.icon_start}, ICON_End={expression.icon_end}{mark}"
        )
    print()


def print_console_help(expressions: Sequence[Expression]) -> None:
    names = " / ".join(expression.name for expression in expressions)
    print(
        f"""
可用命令：
  1 ~ N                       按序号切换表情
  {names}                     按名称切换表情
  list                        显示表情列表
  stop / start / hide          控制当前动画 VP
  hide-vp ADDR                 写指定 VP 为 hide-value
  clear-vps ADDR...             一次隐藏多个 VP
  scan-vp START COUNT [STEP]    逐个隐藏 VP，观察哪个控件消失
  lib ID                       只修改 ICON_Lib，例如 lib 24
  range START END              修改 ICON_Start / ICON_End
  write-word ADDR VALUE        写 Word，例如 write-word 0x5602 0
  write-byte ADDR VALUE        写 Byte，例如 write-byte 0x8009 0x18
  raw ADDR VALUE                写 Word，兼容旧脚本
  test                         依次测试全部表情
  help                         显示帮助
  q                            退出
"""
    )


def run_console(animation: AnimationIconControl, switcher: ExpressionSwitcher) -> None:
    print("T5L DGUSII 表情控制台，输入 help 查看命令。\n")
    show_expressions(switcher.list_expressions(), switcher.current)

    while True:
        command = input("t5l> ").strip()
        if not command:
            continue

        lower = command.lower()
        parts = command.split()

        if lower in ("q", "quit", "exit"):
            return
        if lower in ("help", "h", "?"):
            print_console_help(switcher.list_expressions())
            continue
        if lower in ("list", "ls"):
            show_expressions(switcher.list_expressions(), switcher.current)
            continue
        if lower == "stop":
            animation.stop()
            continue
        if lower == "start":
            animation.start()
            continue
        if lower == "hide":
            animation.hide()
            continue
        if lower == "test":
            switcher.run_test()
            continue

        try:
            if lower.startswith("hide-vp "):
                animation.hide_vp(parse_int(parts[1]))
            elif lower.startswith("clear-vps "):
                for item in parts[1:]:
                    animation.hide_vp(parse_int(item))
            elif lower.startswith("scan-vp "):
                if len(parts) not in (3, 4):
                    raise ValueError("usage: scan-vp 0x5600 8 or scan-vp 0x5600 8 2")
                start_addr = parse_int(parts[1])
                count = parse_int(parts[2])
                step = parse_int(parts[3]) if len(parts) == 4 else 2
                for index in range(count):
                    addr = start_addr + index * step
                    input(f"按回车隐藏 VP=0x{addr:04X} ...")
                    animation.hide_vp(addr)
            elif lower.startswith("lib "):
                animation.write_icon_lib(parse_int(parts[1]))
            elif lower.startswith("range "):
                animation.write_icon_range(parse_int(parts[1]), parse_int(parts[2]))
            elif lower.startswith("write-word "):
                animation.client.write_words(parse_int(parts[1]), [parse_int(parts[2])], "manual word")
            elif lower.startswith("write-byte "):
                animation.client.write_bytes(parse_int(parts[1]), [parse_int(parts[2])], "manual byte")
            elif lower.startswith("raw "):
                animation.client.write_words(parse_int(parts[1]), [parse_int(parts[2])], "raw")
            else:
                expression = switcher.switch(lower)
                print(f"已切换：{expression.name}")
        except Exception as exc:
            print(f"执行失败：{exc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_ports:
        print_ports()
        return 0

    command = args.command or "console"

    if command == "list":
        show_expressions(resolve_expressions(args, resolve_config(args)))
        return 0

    client: DgusClient | None = None
    try:
        client, animation, switcher = create_switcher(args)

        if command == "console":
            run_console(animation, switcher)
        elif command == "switch":
            expression = switcher.switch(args.expression)
            print(f"已切换：{expression.name}")
        elif command == "lib":
            animation.write_icon_lib(parse_int(args.icon_lib))
        elif command == "write-word":
            client.write_words(parse_int(args.addr), [parse_int(args.value)], "manual word")
        elif command == "raw":
            client.write_words(parse_int(args.addr), [parse_int(args.value)], "raw")
        elif command == "write-byte":
            client.write_bytes(parse_int(args.addr), [parse_int(args.value)], "manual byte")
        elif command == "hide-vp":
            animation.hide_vp(parse_int(args.addr))
        else:
            parser.error(f"unknown command: {command}")
    except serial.SerialException as exc:
        print(f"串口错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130
    finally:
        if client is not None:
            client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
