"""Small, locked handoff log and guarded publication; codeg owns scheduling."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROLES = ("prepare", "gemini", "table", "writer", "verify", "review", "final")
STATUSES = {"pending", "running", "waiting_input", "waiting_user", "done", "failed", "canceled"}
DEPENDENCIES = {"prepare": (), "gemini": ("prepare",), "table": ("prepare",),
                "writer": ("prepare",), "verify": ("gemini",),
                "review": ("table", "writer", "verify"), "final": ("review",)}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def locked(run_dir):
    """OS-owned lock: process death releases it; no stale-age lock stealing."""
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / ".运行记录.lock"
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        until = time.monotonic() + 10
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise TimeoutError("交接记录被占用，请稍后续做")
                time.sleep(0.05)
        try:
            yield run_dir / "运行记录.json"
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("交接路径必须是绝对路径：" + str(value))
    return str(path.resolve())


def init(run_dir, spec):
    with locked(run_dir) as path:
        if path.exists():
            old = read_json(path)
            if old["package"] != spec["package"]:
                raise ValueError("本运行目录已属于其他资料包")
            requested = [(b["id"], b["name"], b["info"], b.get("outputs", {}), b.get("inputs", [])) for b in spec["batches"]]
            existing = [(b["id"], b["name"], b["info"], b["output_paths"], list(b["inputs"])) for b in old["batches"].values()]
            normalized = [(i, n, absolute(info), {k: absolute(v) for k, v in o.items()}, [absolute(v) for v in ins]) for i, n, info, o, ins in requested]
            if normalized != existing:
                raise ValueError("批次或输入版本改变，使用新运行目录，保留旧记录")
            check_inputs(old)
            return old
        state = {"version": 2, "package": spec["package"], "updated_at": now(), "batches": {}, "history": []}
        if not spec.get("batches"):
            raise ValueError("批次清单为空")
        for item in spec["batches"]:
            bid = item["id"]
            if not isinstance(bid, str) or not bid or bid in state["batches"]:
                raise ValueError("批次 ID 缺失或重复")
            inputs = {absolute(p): digest(p) for p in item["inputs"]}
            if not inputs:
                raise ValueError("每批至少登记一个原件文件作为版本依据")
            outputs = {k: absolute(v) for k, v in item["outputs"].items()}
            if set(outputs) != {"audit", "materials", "opinion"}:
                raise ValueError("成果清单须明确两表和 Word：audit/materials/opinion")
            if set(outputs.values()) & set(inputs):
                raise ValueError("成果路径与原件重合")
            state["batches"][bid] = {"id": bid, "name": item["name"], "inputs": inputs,
                "info": absolute(item["info"]), "output_paths": outputs,
                "user_records": bool(item.get("user_records", False)),
                "initial_existing": {p: digest(p) for p in outputs.values() if Path(p).is_file()},
                "roles": {r: {"status": "pending", "task_id": None, "artifacts": {}, "pending": []} for r in ROLES},
                "published": {}, "summary": "待准备"}
        all_outputs = [p for b in state["batches"].values() for p in b["output_paths"].values()]
        all_inputs = {p for b in state["batches"].values() for p in b["inputs"]}
        if len(all_outputs) != len(set(all_outputs)) or all_inputs.intersection(all_outputs):
            raise ValueError("批次成果路径重复或覆盖其他批次原件")
        atomic_json(path, state)
        return state


def check_inputs(state, bid=None):
    batches = [state["batches"][bid]] if bid is not None else state["batches"].values()
    for batch in batches:
        for path, sha in batch["inputs"].items():
            if not Path(path).is_file() or digest(path) != sha:
                raise ValueError("输入版本已变化，另开运行目录重新登记：" + path)


def owner_lock(path):
    path = Path(path)
    return any((path.parent / name).exists() for name in {"~$" + path.name, "~$" + path.name[2:]})


def guard_reason(batch, path):
    path = Path(path)
    if str(path) not in batch["output_paths"].values():
        return "未登记为本批成果"
    if "initial_existing" not in batch:
        return "旧记录缺少初始来源分类，保留全部原稿，另开运行目录"
    if str(path) in batch["initial_existing"]:
        return "登记时已有历史成果，移动或删除后也不自动补回"
    if owner_lock(path):
        return "Office 占用标记存在"
    baseline = batch["published"].get(str(path))
    if not path.exists():
        return "已发布文件被删除，保留人工删除" if baseline else None
    if not baseline:
        return "历史成果没有可靠自动版本，保留原稿"
    if digest(path) != baseline["sha256"]:
        return "成果已被修改，保留原稿并另列建议"
    return None


@contextmanager
def exclusive_file(path):
    """Windows share=0 keeps WPS/Excel from modifying a file during publication."""
    path = Path(path)
    exists = path.exists()
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0xC0000000, 0, None, 3 if exists else 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "文件被占用或不可写：" + str(path))
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        stream = os.fdopen(fd, "r+b")
    else:
        import fcntl
        stream = path.open("r+b" if exists else "x+b")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise
    try:
        yield stream, exists
    finally:
        stream.close()


def guard(run_dir, bid, file, candidate=None, role=None):
    file = absolute(file)
    with locked(run_dir) as state_path:
        state = read_json(state_path)
        check_inputs(state, bid)
        batch = state["batches"][bid]
        def reject(reason):
            if candidate is not None:
                batch.setdefault("delivery_pending", {})[file] = {"candidate": absolute(candidate), "reason": reason}
                batch["summary"] = "候选稿待交付"
                state["updated_at"] = now()
                atomic_json(state_path, state)
            return {"allowed": False, "published": False, "reason": reason, "file": file}
        reason = guard_reason(batch, file)
        if reason:
            return reject(reason)
        if candidate is None:
            return {"allowed": True, "file": file, "note": "检查结果仅供参考；发布使用 --candidate 再次核验"}
        if role not in ROLES or batch["roles"][role]["status"] != "running":
            raise ValueError("发布必须属于正在运行的角色")
        if role not in {"table", "writer", "review"}:
            raise ValueError("本角色不负责发布业务成果")
        kind = next(k for k, p in batch["output_paths"].items() if p == file)
        if (role == "table" and kind == "opinion") or (role == "writer" and kind != "opinion"):
            raise ValueError("发布超出本角色职责")
        candidate = Path(absolute(candidate))
        if str(candidate) == file or str(candidate) in {p for b in state["batches"].values() for p in b["inputs"]}:
            raise ValueError("候选文件须为另存的工作副本")
        data = candidate.read_bytes()
        if not data:
            raise ValueError("候选文件为空")
        target = Path(file)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with exclusive_file(target) as (stream, existed):
                original = stream.read()
                old = batch["published"].get(file)
                if existed and (not old or hashlib.sha256(original).hexdigest() != old["sha256"]):
                    return reject("发布前发现人工改动，保留原稿")
                if existed:
                    backup = state_path.parent / "备份" / (old["sha256"] + "_" + target.name)
                    backup.parent.mkdir(exist_ok=True)
                    if not backup.exists():
                        backup.write_bytes(original)
                try:
                    stream.seek(0)
                    stream.write(data)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    stream.seek(0)
                    if stream.read() != data:
                        raise OSError("发布后的磁盘读回不一致")
                except OSError:
                    stream.seek(0)
                    stream.write(original)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    raise
            expected = hashlib.sha256(data).hexdigest()
            if digest(target) != expected:
                return reject("发布后文件已变化，保留候选稿并重新只读核验")
        except OSError as error:
            return reject(str(error))
        batch["published"][file] = {"sha256": expected, "readback_sha256": expected,
                                   "readback_at": now(), "role": role, "at": now()}
        batch.get("delivery_pending", {}).pop(file, None)
        batch["summary"] = summary(batch)
        state["updated_at"] = now()
        atomic_json(state_path, state)
        return {"allowed": True, "published": True, "readback_verified": True,
                "file": file, "sha256": batch["published"][file]["sha256"]}


def ready(batch, role):
    return all(batch["roles"][r]["status"] == "done" for r in DEPENDENCIES[role]) and (
        role != "writer" or batch["user_records"] or batch["roles"]["gemini"]["status"] == "done")


def summary(batch):
    roles = batch["roles"]
    if batch.get("delivery_pending"):
        return "候选稿待交付"
    if roles["final"]["status"] == "done":
        return "验收完成"
    if any(r["status"] == "waiting_user" for r in roles.values()):
        return "待人工判断"
    if roles["writer"]["status"] == "done":
        return "初稿已交付，核实未完成" if roles["verify"]["status"] != "done" else "初稿及核实完成，待复查验收"
    return "处理中"


def record(run_dir, event):
    with locked(run_dir) as path:
        state = read_json(path)
        check_inputs(state, event["batch"])
        batch = state["batches"][event["batch"]]
        role, status = event["role"], event["status"]
        if role not in ROLES or status not in STATUSES:
            raise ValueError("未知角色或状态")
        if "user_records" in event:
            if role != "prepare" or not isinstance(event["user_records"], bool):
                raise ValueError("用户记录就绪由 prepare 按实际来源登记布尔值")
            batch["user_records"] = event["user_records"]
        current = batch["roles"][role]
        task_id = event.get("task_id")
        active_statuses = {"running", "waiting_input", "waiting_user"}
        if current["status"] in active_statuses and current["task_id"] and current["task_id"] != task_id:
            raise ValueError("旧任务仍运行，确认终止并登记 canceled 后才能接手")
        if status == "running":
            if not task_id or not ready(batch, role):
                raise ValueError("未给任务 ID 或输入尚未就绪")
            if current["status"] == "done" and not (role in {"review", "final"} and event.get("recheck")):
                raise ValueError("已完成角色不重复派单；修订由 review 负责")
            for other in state["batches"].values():
                active = other["roles"][role]
                if active["status"] in active_statuses and active["task_id"] and active["task_id"] != task_id:
                    raise ValueError("同一角色已有其他活跃代理")
        if status == "done" and (current["status"] != "running" or task_id != current["task_id"]):
            raise ValueError("完成必须来自当前运行任务")
        artifacts = {absolute(p): digest(p) for p in event.get("artifacts", [])}
        if status == "done":
            if not artifacts or event.get("pending"):
                raise ValueError("有未核事项或没有交接文件，不能标记完成")
            required_kinds = {"table": ("audit", "materials"), "writer": ("opinion",)}.get(role, ())
            for kind in required_kinds:
                target = batch["output_paths"].get(kind)
                published = batch["published"].get(target, {})
                baseline = published.get("sha256") or batch.get("initial_existing", {}).get(target)
                if target not in artifacts or baseline is None or digest(target) != baseline:
                    raise ValueError("未交接受保护发布文件或只读确认的历史原稿：" + kind)
                if target in batch.get("delivery_pending", {}) or (published and published.get("readback_sha256") != baseline):
                    raise ValueError("成果尚未完成当前版本磁盘读回：" + kind)
            if role in {"review", "final"}:
                report_path = absolute(event["check_report"])
                report = read_json(report_path)
                if report.get("overall") != "pass" or report.get("batch") != batch["name"]:
                    raise ValueError("本批检查尚未通过")
                hashes = report.get("source_hashes", {})
                if not set(batch["output_paths"].values()).issubset(hashes):
                    raise ValueError("检查报告未覆盖本批全部成果")
                for p, sha in hashes.items():
                    if digest(p) != sha:
                        raise ValueError("检查后的文件发生变化，重新检查")
                artifacts[report_path] = digest(report_path)
        current.update(status=status, task_id=task_id or current["task_id"], updated_at=now(),
                       artifacts=artifacts or current["artifacts"], pending=event.get("pending", []))
        batch["summary"] = summary(batch)
        state["updated_at"] = now()
        state["history"].append({"at": now(), **event})
        atomic_json(path, state)
        return batch


def status(run_dir):
    state = read_json(Path(run_dir) / "运行记录.json")
    result = []
    for b in state["batches"].values():
        input_changes = [p for p, meta in b["inputs"].items() if not Path(p).is_file() or digest(p) != meta]
        changed = [p for p, meta in b["published"].items() if not Path(p).exists() or digest(p) != meta["sha256"]]
        changed += [p for p, sha in b.get("initial_existing", {}).items() if not Path(p).exists() or digest(p) != sha]
        stale = [r for r in ROLES if b["roles"][r]["status"] == "done" and any(
            not Path(p).is_file() or digest(p) != sha for p, sha in b["roles"][r]["artifacts"].items())]
        result.append({"id": b["id"], "name": b["name"], "summary": "输入已变化，另开运行目录" if input_changes else "人工稿已变化，重新只读复核" if changed else b["summary"],
                       "roles": b["roles"], "ready": [] if input_changes else [r for r in ROLES if ready(b, r) and b["roles"][r]["status"] in {"pending", "waiting_input", "failed", "canceled"}],
                       "input_changes": input_changes, "protected_changes": changed, "stale_roles": stale})
    return {"package": state["package"], "batches": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "status", "record", "guard"])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--input", help="init 批次清单或 record 事件 JSON")
    parser.add_argument("--batch")
    parser.add_argument("--file")
    parser.add_argument("--candidate")
    parser.add_argument("--role", choices=ROLES)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init(args.run_dir, read_json(args.input))
        elif args.command == "record":
            result = record(args.run_dir, read_json(args.input))
        elif args.command == "guard":
            result = guard(args.run_dir, args.batch, args.file, args.candidate, args.role)
        else:
            result = status(args.run_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("allowed") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
