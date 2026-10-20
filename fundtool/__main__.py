"""命令行入口。

    python -m fundtool [--host H] [--port P] [--mock] [--doctor]

兼容 README 的位置参数写法：python -m fundtool doctor。
--mock 先置位 config.MOCK 再导入 app，确保 MockClient 被选中。
"""
import argparse
import sys

from . import config


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fundtool",
        description="财富终端 · 基金 —— 基于 AKShare 的个人基金工具")
    parser.add_argument("command", nargs="?", choices=["doctor"],
                        help="子命令：doctor=环境自检（README 写法，等价 --doctor）")
    parser.add_argument("--host", default=config.DEFAULT_HOST,
                        help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT,
                        help="监听端口（默认 8080）")
    parser.add_argument("--mock", action="store_true",
                        help="离线演示：使用内置样例数据（不访问外网）")
    parser.add_argument("--doctor", action="store_true",
                        help="运行环境自检后退出（0=通过 / 1=失败）")
    args = parser.parse_args(argv)

    if args.mock:
        config.MOCK = True

    if args.doctor or args.command == "doctor":
        from .doctor import run_doctor
        sys.exit(run_doctor())

    from .api import app   # 延迟导入：保证 config.MOCK 先生效
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
