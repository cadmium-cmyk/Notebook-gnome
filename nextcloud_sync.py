import os
import base64
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import NamedTuple


REMOTE_FOLDER_NAME = "Notebooks"


class NextcloudSyncResult(NamedTuple):
    success: bool
    uploaded: int
    downloaded: int
    message: str

    @property
    def ok(self) -> bool:
        return self.success

    @property
    def msg(self) -> str:
        return self.message



class NextcloudSyncClient:
    """
    WebDAV synchronization client for Nextcloud `/remote.php/dav/files/[username]/Notebooks/`.
    Recursively synchronizes the entire local directory structure including hidden files (.assets, etc.)
    and subfolders.
    """

    def __init__(self, server_url: str, username: str, app_password: str):
        self.server_url = self._normalize_server_url(server_url)
        self.username = (username or "").strip()
        self.app_password = app_password or ""

    @staticmethod
    def _normalize_server_url(url: str) -> str:
        s = (url or "").strip()
        if not s:
            return ""
        if not s.startswith(("http://", "https://")):
            s = "https://" + s
        return s.rstrip("/")

    def get_notebooks_dav_url(self) -> str:
        encoded_user = urllib.parse.quote(self.username, safe="")
        return f"{self.server_url}/remote.php/dav/files/{encoded_user}/{REMOTE_FOLDER_NAME}"

    def _auth_header(self) -> str:
        token = f"{self.username}:{self.app_password}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")

    def _request(self, method: str, url: str, data: bytes = None, headers: dict = None, timeout: int = 30):
        hdrs = {
            "Authorization": self._auth_header(),
            "User-Agent": "xnotes-gnome/1.0",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        return urllib.request.urlopen(req, timeout=timeout)

    @staticmethod
    def _rel_path_to_url(base_url: str, rel_path: str) -> str:
        norm = rel_path.replace(os.sep, "/").strip("/")
        parts = [urllib.parse.quote(seg, safe="") for seg in norm.split("/") if seg]
        joined = "/".join(parts)
        return f"{base_url.rstrip('/')}/{joined}"

    def ensure_notebooks_folder(self) -> tuple[bool, str]:
        if not self.server_url or not self.username or not self.app_password:
            return False, "Please enter Nextcloud Server URL, Username, and App Password."
        base_url = self.get_notebooks_dav_url() + "/"
        try:
            with self._request("PROPFIND", base_url, headers={"Depth": "0"}) as resp:
                if resp.status in (200, 207):
                    return True, "Connected to Nextcloud /Notebooks folder."
        except urllib.error.HTTPError as e:
            if e.code == 404:
                try:
                    with self._request("MKCOL", base_url) as mk_resp:
                        if 200 <= mk_resp.status < 300:
                            return True, "Created and connected to Nextcloud /Notebooks folder."
                except urllib.error.HTTPError as mk_err:
                    return False, f"Failed to create /Notebooks folder (HTTP {mk_err.code})."
            elif e.code in (401, 403):
                return False, f"Authentication failed (HTTP {e.code}). Check username and app password."
            else:
                return False, f"Nextcloud returned HTTP {e.code}."
        except Exception as e:
            return True if "207" in str(e) else False, f"Connection error: {e}"
        return True, "Connected to Nextcloud /Notebooks folder."

    def _parse_propfind_xml(self, xml_bytes: bytes, base_path: str, remote_files: dict, remote_dirs: set):
        root = ET.fromstring(xml_bytes)
        ns = {"d": "DAV:"}
        for response in root.findall("d:response", ns):
            href_el = response.find("d:href", ns)
            if href_el is None or not href_el.text:
                continue
            raw_path = urllib.parse.urlparse(href_el.text).path
            if not raw_path.startswith(base_path):
                continue
            rel = urllib.parse.unquote(raw_path[len(base_path):]).strip("/")
            if not rel:
                continue

            prop = response.find(".//d:prop", ns)
            if prop is None:
                continue

            res_type = prop.find("d:resourcetype", ns)
            is_col = (res_type is not None and res_type.find("d:collection", ns) is not None)
            if is_col:
                remote_dirs.add(rel)
            else:
                lastmod_el = prop.find("d:getlastmodified", ns)
                mtime = 0.0
                if lastmod_el is not None and lastmod_el.text:
                    try:
                        mtime = parsedate_to_datetime(lastmod_el.text.strip()).timestamp()
                    except Exception:
                        mtime = 0.0
                size_el = prop.find("d:getcontentlength", ns)
                size = int(size_el.text.strip()) if (size_el is not None and size_el.text and size_el.text.strip().disdigit()) else 0
                remote_files[rel] = {
                    "rel_path": rel,
                    "mtime": mtime,
                    "size": size,
                }

    def list_remote_tree(self) -> tuple[dict[str, dict], set[str]]:
        base_url = self.get_notebooks_dav_url()
        base_path = urllib.parse.urlparse(base_url).path.rstrip("/") + "/"
        remote_files = {}
        remote_dirs = set()
        propfind_xml = (
            b'<?xml version="1.0" encoding="utf-8" ?>'
            b'<d:propfind xmlns:d="DAV:">'
            b'<d:prop><d:displayname/><d:getlastmodified/><d:getcontentlength/><d:resourcetype/></d:prop>'
            b'</d:propfind>'
        )
        try:
            with self._request(
                "PROPFIND",
                base_url + "/",
                data=propfind_xml,
                headers={"Depth": "infinity", "Content-Type": "application/xml; charset=utf-8"},
            ) as resp:
                if resp.status in (200, 207):
                    self._parse_propfind_xml(resp.read(), base_path, remote_files, remote_dirs)
                    return remote_files, remote_dirs
        except Exception:
            pass

        remote_files.clear()
        remote_dirs.clear()
        dir_queue = [""]
        visited = set()

        while dir_queue:
            cur_dir = dir_queue.pop(0)
            if cur_dir in visited:
                continue
            visited.add(cur_dir)
            cur_url = (self._rel_path_to_url(base_url, cur_dir) + "/") if cur_dir else (base_url + "/")
            try:
                with self._request(
                    "PROPFIND",
                    cur_url,
                    data=propfind_xml,
                    headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                ) as resp:
                    if resp.status in (200, 207):
                        new_dirs = set()
                        self._parse_propfind_xml(resp.read(), base_path, remote_files, new_dirs)
                        for d in new_dirs:
                            if d not in visited and d not in dir_queue:
                                dir_queue.append(d)
                                remote_dirs.add(d)
            except Exception:
                pass

        return remote_files, remote_dirs

    def _ensure_remote_parent_dirs(self, rel_dir: str, known_remote_dirs: set[str], base_url: str):
        if not rel_dir or rel_dir in known_remote_dirs:
            return
        parts = [p for p in rel_dir.replace(os.sep, "/").strip("/").split("/") if p]
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}" if cur else part
            if cur not in known_remote_dirs:
                url = self._rel_path_to_url(base_url, cur) + "/"
                try:
                    with self._request("MKCOL", url) as resp:
                        pass
                except urllib.error.HTTPError as e:
                    if e.code not in (200, 201, 204, 405):
                        pass
                except Exception:
                    pass
                known_remote_dirs.add(cur)

    def sync_folder(self, local_dir: str) -> NextcloudSyncResult:
        if not local_dir or not os.path.isdir(local_dir):
            return NextcloudSyncResult(False, 0, 0, "Local notebook folder is not open.")

        ok, msg = self.ensure_notebooks_folder()
        if not ok:
            return NextcloudSyncResult(False, 0, 0, msg)

        try:
            remote_files, remote_dirs = self.list_remote_tree()
            local_files = {}

            for root, dirs, files in os.walk(local_dir):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                for file_name in files:
                    if file_name in ("__pycache__", ".DS_Store"):
                        continue
                    full_path = os.path.join(root, file_name)
                    rel_path = os.path.relpath(full_path, local_dir).replace(os.sep, "/")
                    try:
                        st = os.stat(full_path)
                        local_files[rel_path] = {
                            "path": full_path,
                            "mtime": st.st_mtime,
                            "size": st.st_size,
                        }
                    except OSError:
                        pass

            base_url = self.get_notebooks_dav_url()
            uploaded = 0
            downloaded = 0

            # 1. Upload local files that are missing or newer on disk
            for rel_path, linfo in local_files.items():
                rinfo = remote_files.get(rel_path)
                remote_url = self._rel_path_to_url(base_url, rel_path)
                if rinfo is None or linfo["mtime"] > rinfo["mtime"] + 2.0:
                    parent_dir = os.path.dirname(rel_path)
                    if parent_dir:
                        self._ensure_remote_parent_dirs(parent_dir, remote_dirs, base_url)
                    with open(linfo["path"], "rb") as f:
                        payload = f.read()
                    with self._request(
                        "PUT",
                        remote_url,
                        data=payload,
                        headers={"Content-Type": "application/octet-stream"},
                    ) as resp:
                        if 200 <= resp.status < 300:
                            uploaded += 1


            # 2. Download remote files that are missing or newer on server
            for rel_path, rinfo in remote_files.items():
                linfo = local_files.get(rel_path)
                remote_url = self._rel_path_to_url(base_url, rel_path)
                if linfo is None or rinfo["mtime"] > linfo["mtime"] + 2.0:
                    with self._request("GET", remote_url) as resp:
                        if 200 <= resp.status < 300:
                            payload = resp.read()
                            dest_path = os.path.join(local_dir, rel_path)
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            with open(dest_path, "wb") as f:
                                f.write(payload)
                            if rinfo["mtime"] > 0:
                                try:
                                    os.utime(dest_path, (rinfo["mtime"], rinfo["mtime"]))
                                except Exception:
                                    pass
                            downloaded += 1


            return NextcloudSyncResult(
                True,
                uploaded,
                downloaded,
                f"Synced with Nextcloud /Notebooks (\u2191{uploaded} uploaded, \u2193{downloaded} downloaded)",
            )
        except Exception as e:
            return NextcloudSyncResult(False, 0, 0, f"Nextcloud sync failed: {e}")
