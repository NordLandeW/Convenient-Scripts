"""Single-chain unwrapping and per-input cleanup.

Only one significant logical file continues the chain. Multiple candidates are
terminal content, and a successfully unwrapped child deliberately replaces all
of its parent's wrapper contents, including ignored small files and directories.
"""

import os
import shutil

import send2trash

import extract_hidden_zip as hiddenZip
from console import print_info, print_warning
from extraction import (
    extract_with_7zip,
    handle_bandizip_extraction,
    manual_password_entry,
    try_passwords,
)
from housekeeping import (
    RECOVER_SUFFIX,
    _normalize_path_for_compare,
    create_unique_directory,
    move_temp_folders_to_recycle_bin,
    remove_autodec_files,
)
from output import publish_output
from structure import (
    filter_non_primary_split_inputs,
    get_archive_base_name,
    group_archive_files,
    is_likely_archive_filename,
    list_related_archive_parts,
)

DEFAULT_EMBEDDED_SCAN_MAX_LEVEL = 2
SMALL_NON_ARCHIVE_IGNORE_THRESHOLD = 20 * 1024


def try_remove_directory(directory):
    try:
        shutil.rmtree(directory)
    except Exception:
        pass


class ExtractionWorkflow:
    def __init__(
        self,
        password_book,
        *,
        embedded_scan_depth=DEFAULT_EMBEDDED_SCAN_MAX_LEVEL,
        flatten_single_file=True,
        trash_on_success=True,
        extract_to_base_folder=False,
        use_binwalk=False,
    ):
        self.password_book = password_book
        self.embedded_scan_depth = max(0, embedded_scan_depth)
        self.auto_flatten_single_file = flatten_single_file
        self.trash_on_success = trash_on_success
        self.extract_to_base_folder = extract_to_base_folder
        self.last_success_password = None
        # Per-input state: a path can name the source first, then the output.
        self.recycled_reserved_paths = set()
        hiddenZip.USE_BINWALK = bool(use_binwalk)

    def recursive_extract(
        self,
        base_folder,
        file_path,
        last_success_password=None,
        level=1,
        embedded_scan_depth=DEFAULT_EMBEDDED_SCAN_MAX_LEVEL,
        source_archive_paths: set = None,
    ):
        """Unwrap one layer, preserving the original parent/child protocol.

        True asks the parent to retain this input (unopenable, skipped or failed).
        False means this call or a descendant published the result, so its parent
        may discard the wrapper. At the top level only False permits source trash.
        This is deliberately not a conventional success boolean.
        """
        source_archive_paths = set(source_archive_paths or [])
        temp_folder = create_unique_directory(base_folder, "temp_extract")
        orig_temp_folder = temp_folder
        last_compressed_file_name = get_archive_base_name(file_path)

        passwords = sorted(
            self.password_book.passwords.items(), key=lambda item: item[1], reverse=True
        )
        if last_success_password is not None:
            password = last_success_password
        elif passwords:
            password = passwords[0][0]
        else:
            password = None

        while True:
            try_result = extract_with_7zip(file_path, temp_folder, password)
            if try_result == -1:
                next_password = try_passwords(file_path, temp_folder, passwords, password)
                if next_password is None:
                    name_pwd = last_compressed_file_name
                    if name_pwd and name_pwd != password:
                        print_info(f"Trying archive name '{name_pwd}' as password...")
                        if extract_with_7zip(file_path, temp_folder, name_pwd) > 0:
                            next_password = name_pwd
                if next_password is None:
                    next_password = manual_password_entry(file_path, temp_folder, level)
                if next_password is None:
                    print_warning(f"用户跳过了文件 {file_path} 的密码输入喵，将跳过该文件。")
                    try_remove_directory(orig_temp_folder)
                    return True
                password = next_password
                break
            elif try_result == -2:
                if file_path.endswith(RECOVER_SUFFIX):
                    new_password = handle_bandizip_extraction(
                        file_path, temp_folder, passwords, level
                    )
                    if new_password:
                        password = new_password
                        break
                    print_warning("Bandizip 也无法处理这个文件喵。")
                    try_remove_directory(orig_temp_folder)
                    return True

                found_embedded = False
                for fmt in ["zip", "rar", "7z", "*"]:
                    if (
                        level <= embedded_scan_depth
                        and RECOVER_SUFFIX not in file_path
                        and hiddenZip.has_embedded_signature(file_path, fmt)
                    ):
                        print_info(
                            f"Found embedded {fmt.upper() if fmt != '*' else 'file'}, extracting..."
                        )
                        hiddenZip.extract_embedded_file(
                            file_path, file_path + RECOVER_SUFFIX, fmt
                        )
                        file_path = file_path + RECOVER_SUFFIX
                        found_embedded = True
                        break
                if found_embedded:
                    continue
                try_remove_directory(orig_temp_folder)
                return True
            elif try_result <= 0:
                # An extractor failure is not a successfully peeled layer. The
                # parent may retain this archive; top-level sources stay intact.
                try_remove_directory(orig_temp_folder)
                return True
            else:
                break

        self.last_success_password = password
        self.password_book.add_password(password)

        try:
            grouped_files = group_archive_files(temp_folder)
            while len(grouped_files) == 0 and len(os.listdir(temp_folder)) == 1:
                only_item_name = os.listdir(temp_folder)[0]
                deeper_folder = os.path.join(temp_folder, only_item_name)
                if os.path.isdir(deeper_folder):
                    temp_folder = deeper_folder
                    last_compressed_file_name = os.path.basename(temp_folder)
                    grouped_files = group_archive_files(temp_folder)
                else:
                    break
        except FileNotFoundError:
            grouped_files = []

        filtered_grouped_files = []
        for filename in grouped_files:
            full_path = os.path.join(temp_folder, filename)
            if not os.path.isfile(full_path):
                continue
            if not is_likely_archive_filename(filename):
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = SMALL_NON_ARCHIVE_IGNORE_THRESHOLD + 1
                if size <= SMALL_NON_ARCHIVE_IGNORE_THRESHOLD:
                    continue
            filtered_grouped_files.append(filename)
        grouped_files = filtered_grouped_files

        finished = False
        if len(grouped_files) == 1:
            new_file_path = os.path.join(temp_folder, grouped_files[0])
            finished = self.recursive_extract(
                base_folder,
                new_file_path,
                password,
                level + 1,
                embedded_scan_depth=embedded_scan_depth,
                source_archive_paths=source_archive_paths,
            )
            if not finished:
                try:
                    os.remove(new_file_path)
                except Exception:
                    pass
        else:
            finished = True

        if finished:
            publish_output(
                temp_folder,
                base_folder,
                last_compressed_file_name,
                auto_flatten_single_file=self.auto_flatten_single_file,
                extract_to_base_folder=self.extract_to_base_folder,
                source_archive_paths=source_archive_paths,
                trash_on_success=self.trash_on_success,
                recycled_reserved_paths=self.recycled_reserved_paths,
            )

        # A child that published the result owns the useful payload. Everything
        # remaining in this original temporary root is intentional wrapper data.
        try_remove_directory(orig_temp_folder)
        return False

    def process_files(self, file_paths):
        """Process one input batch; password reuse carries across batches."""
        current_batch = filter_non_primary_split_inputs(file_paths)
        for file_path in current_batch:
            if file_path.lower().endswith(".apk"):
                print_info(f"跳过 .apk 文件：{file_path} 喵。")
                continue
            print_info(f"开始解压文件 {file_path} 喵❤")
            self.recycled_reserved_paths.clear()
            base_folder = os.path.dirname(file_path)
            if move_temp_folders_to_recycle_bin(base_folder):
                print_info("检测到上一次非正常退出留下的临时文件夹喵！已经把它们全部移动到回收站了喵☆")
            try:
                source_archive_paths = {
                    _normalize_path_for_compare(path)
                    for path in list_related_archive_parts(file_path)
                }
            except Exception:
                source_archive_paths = {_normalize_path_for_compare(file_path)}

            result = self.recursive_extract(
                base_folder,
                file_path,
                self.last_success_password,
                embedded_scan_depth=self.embedded_scan_depth,
                source_archive_paths=source_archive_paths,
            )
            if result is False and self.trash_on_success:
                try:
                    for path in list_related_archive_parts(file_path):
                        if _normalize_path_for_compare(path) in self.recycled_reserved_paths:
                            continue
                        if os.path.exists(path):
                            send2trash.send2trash(path)
                            print_info(f"已将被解压的原始压缩文件移动到回收站：{path}")
                except Exception as error:
                    print_warning(f"移动原始压缩文件到回收站失败喵：{error}")
            remove_autodec_files(base_folder)
        self.password_book.save_passwords()
