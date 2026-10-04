"""Command-line entry for single-chain archive unwrapping."""

import argparse
import os
import sys
import time
import traceback

from platformdirs import PlatformDirs

from console import console, print_error, print_info, print_warning
from housekeeping import _ensure_directory
from instance_queue import InstanceQueue
from passwords import PasswordBook
from workflow import DEFAULT_EMBEDDED_SCAN_MAX_LEVEL, ExtractionWorkflow

__version__ = "1.2.1"


def str2bool(value):
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in ("true", "t", "1", "yes", "y"):
        return True
    if lowered in ("false", "f", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError("flatten-single-file 期望布尔值喵（true/false）")


def parse_cli_arguments(argv):
    parser = argparse.ArgumentParser(
        prog="auto_decompression",
        description="自动解压压缩包并支持嵌入内容提取的小工具喵",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-e", "--embedded-scan-depth", type=int,
        default=DEFAULT_EMBEDDED_SCAN_MAX_LEVEL, metavar="K",
        help="在递归层级小于等于 K 时尝试检测及提取隐藏嵌入文件，设为 0 可禁用此功能",
    )
    parser.add_argument(
        "--flatten-single-file", type=str2bool, default=True, metavar="{true,false}",
        help="检测到仅包含与压缩包同名的单个文件时是否自动扁平化喵（默认 true）。",
    )
    parser.add_argument(
        "--trash-on-success", type=str2bool, default=True, metavar="{true,false}",
        help="当（递归）解压成功时，将被解压的原始压缩文件（含分卷）移动到回收站喵（默认 true）。",
    )
    parser.add_argument(
        "--config-dir", type=str, default=None,
        help="指定配置文件（不含密码字典）的所在文件夹喵，密码字典将存放在数据目录中。",
    )
    parser.add_argument(
        "--show-paths", action="store_true",
        help="输出当前配置与数据目录的绝对路径并退出。",
    )
    parser.add_argument(
        "--update-dict", action="store_true",
        help="强制从 Gist 拉取最新的密码本并退出程序。",
    )
    parser.add_argument(
        "--check-dict-conflict-on-startup", action="store_true",
        help="程序启动后立即检查本地密码本与 Gist 是否一致；若冲突则交互询问处理方式。",
    )
    parser.add_argument(
        "--use-binwalk", action="store_true",
        help="启用 binwalk 进行隐藏嵌入文件判定喵（默认关闭，使用手写签名搜索）。",
    )
    parser.add_argument(
        "files", nargs="*", help="需解压的压缩文件路径，可直接拖拽物件到脚本上喵",
    )
    return parser.parse_args(argv)


def _run_primary(args, password_book, queue):
    password_book.ensure_gist_config()
    if args.update_dict:
        if password_book.pull_from_gist_if_possible():
            password_book.skip_gist_sync = True
            return 0
        print_error("强制拉取密码本失败喵。")
        return 1

    password_book.check_passwords()
    if args.check_dict_conflict_on_startup:
        password_book.check_dict_conflict_on_startup()
    queue.start()

    workflow = ExtractionWorkflow(
        password_book,
        embedded_scan_depth=args.embedded_scan_depth,
        flatten_single_file=args.flatten_single_file,
        trash_on_success=args.trash_on_success,
        use_binwalk=args.use_binwalk,
    )
    if not args.flatten_single_file:
        print_info("已禁用同名单文件自动扁平化喵。")
    if args.use_binwalk:
        print_info("已启用 binwalk 进行隐藏嵌入文件判定喵。")
    if args.embedded_scan_depth < 0:
        print_warning("嵌入检测层级小于 0 喵，已自动调整为 0（禁用嵌入扫描）。")
    if workflow.embedded_scan_depth == 0:
        print_info("当前已禁用隐藏嵌入文件判定喵。")
    elif workflow.embedded_scan_depth != DEFAULT_EMBEDDED_SCAN_MAX_LEVEL:
        print_info(f"隐藏嵌入文件判定最大层级已调整为 {workflow.embedded_scan_depth} 层喵。")

    files_to_process = list(args.files)
    if not files_to_process:
        print_warning("请拖拽一个文件到这个脚本上进行解压喵！")
        print_info("也可以输入想要添加的密码喵：")
        while True:
            password = input()
            if password == "":
                break
            password_book.add_password(password, 0)
            password_book.save_passwords()
            print_info(f"已添加密码 {password} 喵！")
        # Files submitted while entering passwords still belong to this primary.
        files_to_process = queue.next_batch()

    while files_to_process:
        workflow.process_files(files_to_process)
        files_to_process = queue.next_batch()
    if args.files:
        print_info("解压完成，退出程序喵...")
    return 0


def error_end(error=None):
    print_error(
        f"程序出现错误喵>.< 非常抱歉喵，下面是错误信息喵！\n{traceback.format_exc()}"
    )
    input()


def main(argv=None):
    args = parse_cli_arguments(sys.argv[1:] if argv is None else argv)
    directories = PlatformDirs(appname="auto_decompression", appauthor="NordLandeW")
    config_dir = (
        os.path.abspath(args.config_dir) if args.config_dir else directories.user_config_dir
    )
    data_dir = directories.user_data_dir
    _ensure_directory(config_dir, "配置")
    _ensure_directory(data_dir, "数据")
    if args.show_paths:
        print_info(f"配置目录: {config_dir}")
        print_info(f"数据目录: {data_dir}")
        return 0

    book = None
    try:
        with InstanceQueue(config_dir) as queue:
            if not queue.claim_or_submit(args.files):
                if args.files:
                    print(str(args.files))
                if args.embedded_scan_depth != DEFAULT_EMBEDDED_SCAN_MAX_LEVEL:
                    print_warning("已有实例正在运行，新的嵌入扫描层级参数未被应用喵。请先关闭原实例再重新运行。")
                if args.use_binwalk:
                    print_warning("已有实例正在运行，新的 --use-binwalk 参数未被应用喵。请先关闭原实例再重新运行。")
                print_info("检测到已经有一个实例在运行，已将任务添加到队列中喵！")
                return 0
            book = PasswordBook(config_dir, data_dir, os.path.dirname(os.path.abspath(__file__)))
            result = _run_primary(args, book, queue)
        time.sleep(1)
        return result
    except Exception as error:
        error_end(error)
        return 1
    finally:
        # Only the primary constructs a book. Explicit finalization also makes
        # imports and Windows multiprocessing children free of Gist exit hooks.
        if book is not None:
            book.sync_to_gist_before_exit()


if __name__ == "__main__":
    sys.exit(main())
