"""Password dictionary persistence and the existing interactive Gist workflow.

A book owns both its dictionary and synchronization state. Importing this module
performs no I/O and registers no exit handlers; the primary CLI instance owns
startup and exit synchronization.
"""

import datetime as _dt
import json
import os
import shutil
import sys
import time

import requests

from console import console, print_error, print_info, print_success, print_warning
from housekeeping import _ensure_directory

GIST_CONFIG_FILE = "gist_config.json"
PASSWORD_FILENAME = "dict.json"


class PasswordBook:
    def __init__(self, config_dir, data_dir, script_dir):
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.script_dir = script_dir
        self.passwords = {}
        self.gist_config = None
        self.gist_remote_ts = None
        self.skip_gist_sync = False

    @property
    def path(self):
        return os.path.join(self.data_dir, PASSWORD_FILENAME)

    def _cfg_path(self, filename):
        return os.path.join(self.config_dir, filename)

    def _load_gist_config(self):
        cfg_path = self._cfg_path(GIST_CONFIG_FILE)
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except Exception:
                pass
        return None

    def _save_gist_config(self, cfg):
        cfg_path = self._cfg_path(GIST_CONFIG_FILE)
        try:
            with open(cfg_path, "w", encoding="utf-8") as file:
                json.dump(cfg, file, ensure_ascii=False, indent=4)
        except Exception as error:
            print_warning(f"保存 Gist 配置失败喵：{error}")

    @staticmethod
    def _gist_headers(token):
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    def _fetch_from_gist(self, cfg):
        """Return (dictionary, gist updated_at), or (None, None) on failure."""
        try:
            response = requests.get(
                f"https://api.github.com/gists/{cfg['gist_id']}",
                headers=self._gist_headers(cfg["token"]),
            )
            if response.status_code == 200:
                gist = response.json()
                file_info = gist["files"].get(cfg["file"])
                if file_info and file_info.get("content") is not None:
                    return json.loads(file_info["content"] or "{}"), gist["updated_at"]
        except Exception as error:
            print_warning(f"拉取 Gist 时出错喵：{error}")
        return None, None

    def _update_gist(self, cfg, content_str):
        try:
            payload = {"files": {cfg["file"]: {"content": content_str}}}
            response = requests.patch(
                f"https://api.github.com/gists/{cfg['gist_id']}",
                headers=self._gist_headers(cfg["token"]),
                json=payload,
            )
            return response.status_code == 200
        except Exception as error:
            print_warning(f"更新 Gist 时出错喵：{error}")
            return False

    def _create_new_gist(self, token, file_name):
        payload = {
            "description": "password‑dict sync",
            "public": False,
            "files": {file_name: {"content": "{}"}},
        }
        response = requests.post(
            "https://api.github.com/gists",
            headers=self._gist_headers(token),
            json=payload,
        )
        if response.status_code == 201:
            return response.json()["id"]
        print_error(f"创建 Gist 失败喵：{response.text}")
        sys.exit(1)

    def _setup_gist_interactive(self):
        console.print("[cyan][b]检测到未配置 Gist，同步向导启动喵~")
        console.print("[cyan][b]请输入 GitHub Token（需 gist 权限）喵：", end="")
        token = input().strip()
        console.print("[cyan][b]请输入已有 Gist ID 或直接回车自动创建喵：", end="")
        gist_id = input().strip()
        file_name = PASSWORD_FILENAME
        cfg = {"token": token, "gist_id": gist_id, "file": file_name}

        if gist_id != "":
            has_local_pwd = os.path.exists(self.path)
            remote_dict, remote_ts_str = self._fetch_from_gist(cfg)
            if remote_dict is not None:
                if has_local_pwd:
                    local_mtime = _dt.datetime.fromtimestamp(
                        os.path.getmtime(self.path), tz=_dt.timezone.utc
                    )
                    remote_ts = (
                        _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00"))
                        if remote_ts_str else None
                    )
                    print_info(f"检测到本地密码本（最后修改时间：{local_mtime.astimezone().strftime('%Y-%m-%d %H:%M:%S')}）")
                    print_info(f"远程密码本（最后修改时间：{remote_ts.astimezone().strftime('%Y-%m-%d %H:%M:%S') if remote_ts else '未知'}）")
                    console.print("[cyan][b]请选择操作：[1] 拉取远程密码本 [2] 上传本地密码本 [默认:1]：", end="")
                    choice = input().strip()
                    if choice == "2":
                        # check_passwords() loads the local book after setup;
                        # the primary instance uploads it on exit.
                        print_info("将使用本地密码本并上传到 Gist")
                    else:
                        self.passwords = remote_dict
                        self.gist_remote_ts = remote_ts
                        self.save_passwords()
                        print_success("已从 Gist 拉取密码本喵！")
                else:
                    self.passwords = remote_dict
                    self.gist_remote_ts = (
                        _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00"))
                        if remote_ts_str else None
                    )
                    self.save_passwords()
                    print_success("已从 Gist 拉取密码本喵！")
            else:
                print_warning(f"无法访问指定的 Gist ID：{gist_id}，请检查 ID 是否正确或网络连接是否正常")
                console.print("[cyan][b]是否要创建新的 Gist？[Y/n]：", end="")
                create_new = input().strip().lower()
                if create_new != "n":
                    cfg["gist_id"] = self._create_new_gist(token, file_name)
                    print_success(f"已创建新的私密 Gist：{cfg['gist_id']} 喵！")
        else:
            cfg["gist_id"] = self._create_new_gist(token, file_name)
            print_success(f"已创建新的私密 Gist：{cfg['gist_id']} 喵！")

        self._save_gist_config(cfg)
        return cfg

    def ensure_gist_config(self):
        cfg = self._load_gist_config()
        if cfg is None:
            cfg = self._setup_gist_interactive()
        self.gist_config = cfg
        return cfg

    def read_passwords(self):
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                self.passwords = json.load(file)
        except Exception as error:
            print_warning(f"读取文件错误喵！错误信息：{error}")

    def save_passwords(self):
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(self.passwords, file, ensure_ascii=False, indent=4)
        except Exception as error:
            print_warning(f"保存密码时出错喵！请检查文件权限或路径。错误信息：{error}")

    def pull_from_gist_if_possible(self):
        """Pull the configured remote book; callers decide when to replace local data."""
        if self.gist_config is None:
            return False
        remote_dict, timestamp = self._fetch_from_gist(self.gist_config)
        if remote_dict is not None:
            self.passwords = remote_dict
            self.gist_remote_ts = (
                _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if timestamp else None
            )
            self.save_passwords()
            print_success("已从 Gist 拉取密码本喵！")
            return True
        return False

    def check_passwords(self):
        if not os.path.exists(self.path):
            _ensure_directory(self.data_dir, "数据")
            legacy_paths = (
                os.path.join(self.config_dir, PASSWORD_FILENAME),
                os.path.join(self.script_dir, PASSWORD_FILENAME),
            )
            for legacy_path in legacy_paths:
                if not os.path.exists(legacy_path):
                    continue
                try:
                    with open(legacy_path, "r", encoding="utf-8") as file:
                        legacy_data = json.load(file)
                    if not isinstance(legacy_data, dict):
                        raise ValueError("密码本格式无效：期望 JSON 字典格式")
                    if not all(isinstance(value, int) for value in legacy_data.values()):
                        raise ValueError("密码本格式无效：计数应为整数")
                except Exception as error:
                    print_warning(f"旧密码本格式异常，跳过迁移喵：{error}")
                    continue
                try:
                    shutil.copy2(legacy_path, self.path)
                    print_info(f"已将旧密码本迁移到新的数据目录喵：{legacy_path}")
                except Exception as error:
                    print_warning(f"迁移旧密码本失败喵：{error}")
                break
        if not os.path.exists(self.path):
            if self.pull_from_gist_if_possible():
                return
            self.passwords = {}
        else:
            self.read_passwords()

    def sync_to_gist_before_exit(self):
        if self.gist_config is None or self.skip_gist_sync:
            return
        if not os.path.exists(self.path):
            return
        local_mtime = _dt.datetime.fromtimestamp(
            os.path.getmtime(self.path), tz=_dt.timezone.utc
        )
        _, remote_ts_str = self._fetch_from_gist(self.gist_config)
        remote_ts = (
            _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00"))
            if remote_ts_str else None
        )
        if (
            remote_ts and self.gist_remote_ts
            and remote_ts > self.gist_remote_ts and remote_ts > local_mtime
        ):
            # Keep the existing policy: warn, then upload. Interactive conflict
            # resolution is the separate startup option below.
            print_warning("检测到远程密码本在本次会话期间发生更新，可能与本地冲突喵！")
            print_warning(f"远程最后更新时间：{remote_ts.isoformat()} 本地最后更新时间：{local_mtime.isoformat()}")
        if self._update_gist(
            self.gist_config, json.dumps(self.passwords, ensure_ascii=False, indent=4)
        ):
            print_success("已同步密码本到 Gist 喵！")
        else:
            print_warning("同步到 Gist 失败喵，请稍后重试！")
        time.sleep(1)

    def check_dict_conflict_on_startup(self):
        if self.gist_config is None:
            return
        remote_dict, remote_ts_str = self._fetch_from_gist(self.gist_config)
        if remote_dict is None:
            print_warning("启动检查时无法拉取 Gist 密码本，已跳过冲突检查喵。")
            return
        self.gist_remote_ts = (
            _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00"))
            if remote_ts_str else None
        )
        if remote_dict == self.passwords:
            print_info("启动检查完成：本地密码本与 Gist 一致喵。")
            return
        local_mtime = (
            _dt.datetime.fromtimestamp(os.path.getmtime(self.path), tz=_dt.timezone.utc)
            if os.path.exists(self.path) else None
        )
        print_warning("启动检查发现本地密码本与 Gist 不一致喵！")
        print_info(f"本地最后修改时间：{local_mtime.astimezone().strftime('%Y-%m-%d %H:%M:%S') if local_mtime else '未知'}")
        print_info(f"远程最后修改时间：{self.gist_remote_ts.astimezone().strftime('%Y-%m-%d %H:%M:%S') if self.gist_remote_ts else '未知'}")
        while True:
            console.print(
                "[cyan][b]请选择冲突处理方式：[1] 拉取远程覆盖本地 [2] 上传本地覆盖远程 [3] 暂不处理且本次退出前不自动同步 [默认:1]：",
                end="",
            )
            choice = input().strip()
            if choice in ("", "1"):
                self.passwords = remote_dict
                self.save_passwords()
                print_success("已拉取远程密码本并覆盖本地喵！")
                return
            if choice == "2":
                if self._update_gist(
                    self.gist_config, json.dumps(self.passwords, ensure_ascii=False, indent=4)
                ):
                    refreshed_dict, refreshed_ts_str = self._fetch_from_gist(self.gist_config)
                    if refreshed_dict is not None:
                        self.gist_remote_ts = (
                            _dt.datetime.fromisoformat(refreshed_ts_str.replace("Z", "+00:00"))
                            if refreshed_ts_str else self.gist_remote_ts
                        )
                    print_success("已上传本地密码本到 Gist 喵！")
                else:
                    print_warning("上传本地密码本到 Gist 失败喵，将保留本地内容继续运行。")
                return
            if choice == "3":
                self.skip_gist_sync = True
                print_info("本次会话将跳过退出时自动同步到 Gist。")
                return
            print_warning("输入无效喵，请输入 1、2 或 3。")

    def add_password(self, password, count=1):
        if password is None:
            return
        if password in self.passwords:
            self.passwords[password] += count
        else:
            self.passwords[password] = count
