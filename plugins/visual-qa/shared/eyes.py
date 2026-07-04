# rev. 10

#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0"]
# ///
"""visual-qa "eyes" - a pluggable VLM client for end-to-end visual testing.

Talks to any OpenAI-compatible chat-completions endpoint (vLLM / NIM / cloud) over plain
HTTP+JSON - no vendor SDK. The model is configurable so the backend can be swapped freely:
a UI-strong VLM (Qwen3-VL) for stills, a temporal model (Cosmos-Reason2) for video/flows.

Two ways to run:
  - MCP server:  `eyes.py mcp`  (exposes `look` and `assert_visual` tools)
  - CLI:         `eyes.py health | look | assert`  (for scripting / health checks)

Config (env, overridable per call / CLI flag):
  VISUAL_QA_ENDPOINT       base URL, default http://localhost:8000/v1
  VISUAL_QA_MODEL          default model for stills, default Qwen/Qwen3-VL-8B-Instruct
  VISUAL_QA_VIDEO_MODEL    model for --video, default nvidia/Cosmos-Reason2-8B
  VISUAL_QA_API_KEY        bearer token if the endpoint needs one (optional)
  VISUAL_QA_TIMEOUT        request timeout seconds, default 120
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = os.environ.get("VISUAL_QA_ENDPOINT", "http://localhost:8000/v1")
DEFAULT_MODEL = os.environ.get("VISUAL_QA_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
DEFAULT_VIDEO_MODEL = os.environ.get("VISUAL_QA_VIDEO_MODEL", "nvidia/Cosmos-Reason2-8B")
API_KEY = os.environ.get("VISUAL_QA_API_KEY", "")
TIMEOUT = float(os.environ.get("VISUAL_QA_TIMEOUT", "120"))


# ------------------------------------------------------------------- multi-model registry
def _registry_path() -> str | None:
    cands = [os.environ.get("VISUAL_QA_REGISTRY", ""),
             os.path.join("ai", "memory", "visual-qa-endpoints.json"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "endpoints.json")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _registry() -> list[dict]:
    """Registered serving instances: [{name, model, endpoint, tasks, default?, note?}, ...].

    Read from VISUAL_QA_REGISTRY (path), else ai/memory/visual-qa-endpoints.json (the private
    layer - endpoints carry internal hosts), else an endpoints.json next to this file (standalone
    use). Empty list = single-endpoint mode (env defaults only).
    """
    p = _registry_path()
    if not p:
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("instances", [])
    except (OSError, json.JSONDecodeError):
        return []


def _active() -> dict | None:
    """The ACTIVE instance - the single one every call uses unless --model overrides.

    Selection: VISUAL_QA_ACTIVE (instance name, non-persistent) > the registry entry with
    default: true > the first registry entry. All functions (look/assert/watch) run on this
    one instance; there is no implicit per-task model switching.
    """
    reg = _registry()
    if not reg:
        return None
    want = os.environ.get("VISUAL_QA_ACTIVE", "")
    if want:
        for inst in reg:
            if inst.get("name") == want:
                return inst
    for inst in reg:
        if inst.get("default"):
            return inst
    return reg[0]


def use(name: str) -> dict:
    """Persistently select the active instance: sets default: true on `name` in the registry."""
    p = _registry_path()
    if not p:
        raise RuntimeError("no registry file found (ai/memory/visual-qa-endpoints.json)")
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    names = [i.get("name") for i in doc.get("instances", [])]
    if name not in names:
        raise RuntimeError(f"unknown instance {name!r}; registered: {names}")
    for inst in doc["instances"]:
        inst["default"] = (inst.get("name") == name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    act = _active()
    return {"active": act.get("name"), "model": act.get("model"), "endpoint": act.get("endpoint")}


def _resolve(model: str | None, endpoint: str | None, video: bool = False) -> tuple[str, str]:
    """Route a call: explicit endpoint wins; else an explicitly named instance/model; else the
    single ACTIVE instance; else the env defaults. No implicit per-task switching."""
    if endpoint:
        return (model or (DEFAULT_VIDEO_MODEL if video else DEFAULT_MODEL), endpoint)
    reg = _registry()
    if model:
        for inst in reg:
            if inst.get("model") == model or inst.get("name") == model:
                return (inst["model"], inst["endpoint"])
        return (model, DEFAULT_ENDPOINT)
    act = _active()
    if act:
        return (act["model"], act["endpoint"])
    return (DEFAULT_VIDEO_MODEL if video else DEFAULT_MODEL, DEFAULT_ENDPOINT)


def pick(task: str) -> dict:
    """ADVISORY ONLY: recommend a registered instance for a task (registry order = preference).
    Does not change routing - calls keep using the active instance until `use <name>` (or an
    explicit --model) says otherwise. Returns {name, model, endpoint, why, alternatives, active}."""
    reg = _registry()
    act = _active()
    if not reg:
        return {"name": None, "model": DEFAULT_MODEL, "endpoint": DEFAULT_ENDPOINT,
                "why": "no registry; env defaults", "alternatives": [], "active": None}
    matches = [i for i in reg if task in i.get("tasks", [])] or reg
    best = matches[0]
    return {"name": best.get("name"), "model": best["model"], "endpoint": best["endpoint"],
            "why": best.get("note", f"first registered instance for task '{task}'"),
            "alternatives": [{"name": i.get("name"), "model": i["model"], "note": i.get("note", "")}
                             for i in matches[1:]],
            "active": act.get("name") if act else None}

ASSERT_SYSTEM = (
    "You are a meticulous visual QA inspector. You are shown one or more screenshots (or "
    "sampled video frames) of an application UI. Judge the user's expectation strictly against "
    "what is actually visible. Respond with ONLY a single JSON object, no prose, no code fence: "
    '{"pass": <true|false>, "reason": "<one or two sentences>", "evidence": "<concrete details '
    'you saw, e.g. labels/text/colors/positions>"}. Set pass=false if anything in the expectation '
    "is missing, wrong, or not clearly visible."
)


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _img_data_url(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _video_duration(path: str) -> float | None:
    """Clip duration in seconds via ffprobe, or None if unreadable."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            text=True, stderr=subprocess.DEVNULL).strip()
        dur = float(out)
        return dur if dur > 0 else None
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def _sample_video(path: str, frames: int) -> list[str]:
    """Sample `frames` frames spread evenly across the WHOLE clip using ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found; install it or pass extracted frames as --media images")
    out_dir = tempfile.mkdtemp(prefix="eyes_frames_")
    pattern = os.path.join(out_dir, "f_%03d.png")
    dur = _video_duration(path)
    if dur:
        # even coverage: rate = frames/duration, so N frames span the full clip
        vf = f"fps={max(frames, 1)}/{dur:.3f}"
    else:
        # duration unknown (stream/pipe): fall back to scene-agnostic 1 fps head sampling
        vf = "fps=1"
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path,
           "-vf", vf, "-frames:v", str(frames), "-fps_mode", "vfr", pattern]
    subprocess.run(cmd, check=True)
    out = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.endswith(".png"))
    if not out:
        raise RuntimeError(f"ffmpeg produced no frames from {path}")
    return out


def _sample_video_fps(path: str, fps: float, max_frames: int | None = None) -> list[str]:
    """Sample frames at a fixed rate (frames per second of source time)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found; install it or pass extracted frames as --media images")
    out_dir = tempfile.mkdtemp(prefix="eyes_fps_")
    pattern = os.path.join(out_dir, "f_%04d.png")
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-vf", f"fps={fps}"]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    cmd += ["-fps_mode", "vfr", pattern]
    subprocess.run(cmd, check=True)
    out = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.endswith(".png"))
    if not out:
        raise RuntimeError(f"ffmpeg produced no frames from {path}")
    return out


def _video_data_url(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("video/"):
        mime = "video/mp4"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _reencode_segment(path: str, start: float, dur: float,
                      fps: float | None, scale: int | None) -> str:
    """Cut [start, start+dur) to a temp clip, optionally resampled to `fps` / downscaled to height `scale`.

    Output is MJPEG-in-AVI on purpose: serving containers commonly ship an OpenCV/ffmpeg without an
    H.264 decoder ("Could not find decoder for codec_id=27"), while intra-frame MJPEG decodes
    everywhere. Bigger payloads, but chunks are short.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found; install it to use video features")
    fd, out = tempfile.mkstemp(suffix=".avi", prefix="eyes_seg_")
    os.close(fd)
    vf = []
    if fps:
        vf.append(f"fps={fps}")
    if scale:
        vf.append(f"scale=-2:{scale}")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-an", "-c:v", "mjpeg", "-q:v", "6", out]
    subprocess.run(cmd, check=True)
    return out


def _build_content(media: list[str], question: str, video: bool, frames: int,
                   fps: float | None = None, native: bool = False) -> list[dict]:
    parts: list[dict] = []
    for m in media:
        is_vid = video or _looks_like_video(m)
        if is_vid and native:
            # tier 2: send the video itself; the server samples with timestamp-aware processing.
            # Always re-encode (MJPEG-AVI; see _reencode_segment) so the server can decode it,
            # applying the target fps when given (server frame budget stays in charge).
            src = _reencode_segment(m, 0.0, _video_duration(m) or 3600.0, fps, None)
            parts.append({"type": "video_url", "video_url": {"url": _video_data_url(src)}})
        elif is_vid:
            imgs = _sample_video_fps(m, fps, max_frames=None) if fps else _sample_video(m, frames)
            for img in imgs:
                parts.append({"type": "image_url", "image_url": {"url": _img_data_url(img)}})
        else:
            parts.append({"type": "image_url", "image_url": {"url": _img_data_url(m)}})
    parts.append({"type": "text", "text": question})
    return parts


def _looks_like_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {".mov", ".mp4", ".webm", ".mkv", ".avi", ".gif"}


def chat(messages: list[dict], model: str, endpoint: str, max_tokens: int = 2048) -> str:
    url = endpoint.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {url}: {e.reason}")
    msg = data["choices"][0]["message"]
    # reasoning models behind --reasoning-parser leave content empty and put the text in a
    # reasoning field (named `reasoning_content` or `reasoning` depending on vLLM version)
    return msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""


def _extract_json(text: str) -> dict:
    """Pull the first balanced {...} object out of a model response."""
    if not text:
        raise ValueError("empty model response (reasoning consumed the token budget?)")
    # strip a leading <think>...</think> block some reasoning models emit
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"unbalanced JSON in response: {text[:200]!r}")


def look(media, question, *, model=None, video=False, frames=8, endpoint=None,
         fps=None, native=False) -> str:
    media = [media] if isinstance(media, str) else list(media)
    model, endpoint = _resolve(model, endpoint, video)
    content = _build_content(media, question, video, frames, fps=fps, native=native)
    return chat([{"role": "user", "content": content}], model, endpoint)


def assert_visual(media, expectation, *, model=None, video=False, frames=8, endpoint=None,
                  fps=None, native=False) -> dict:
    media = [media] if isinstance(media, str) else list(media)
    model, endpoint = _resolve(model, endpoint, video)
    content = _build_content(media, f"Expectation to verify: {expectation}", video, frames,
                             fps=fps, native=native)
    messages = [{"role": "system", "content": ASSERT_SYSTEM}, {"role": "user", "content": content}]
    try:
        raw = chat(messages, model, endpoint)
    except RuntimeError as e:
        return {"pass": False, "reason": f"endpoint error: {e}", "evidence": ""}
    try:
        obj = _extract_json(raw)
        return {
            "pass": bool(obj.get("pass")),
            "reason": str(obj.get("reason", "")),
            "evidence": str(obj.get("evidence", "")),
        }
    except (ValueError, json.JSONDecodeError) as e:
        return {"pass": False, "reason": f"could not parse model response: {e}", "evidence": raw[:500]}


def watch(media, *, question=None, expectation=None, fps=1.0, chunk_seconds=None,
          native=True, scale=None, server_frames=16, model=None, endpoint=None,
          max_chunks=64) -> dict:
    """Tier-3 'watch the whole video': split into chunks, judge each at the target fps, aggregate.

    fps picks the temporal resolution (1 = overview, 15+ = flicker/glitch hunting). Chunk length
    defaults to server_frames/fps (native) or 8/fps (image mode) so every sampled frame is actually
    seen - full coverage regardless of clip length. Exactly one of question/expectation:
    question -> per-chunk notes + a synthesized summary; expectation -> all chunks must pass,
    failing time ranges reported.
    """
    if (question is None) == (expectation is None):
        raise ValueError("watch: pass exactly one of question= or expectation=")
    path = media if isinstance(media, str) else list(media)[0]
    dur = _video_duration(path)
    if not dur:
        raise RuntimeError(f"cannot read duration of {path}")
    if chunk_seconds is None:
        budget = server_frames if native else 8
        chunk_seconds = max(1.0, budget / fps)
    n = math.ceil(dur / chunk_seconds)
    if n > max_chunks:
        raise RuntimeError(f"watch: {n} chunks > max_chunks={max_chunks} - raise chunk_seconds/"
                           f"max_chunks or lower fps (clip {dur:.0f}s @ {fps}fps)")
    model, endpoint = _resolve(model, endpoint, video=True)
    chunks = []
    for i in range(n):
        start = i * chunk_seconds
        d = min(chunk_seconds, dur - start)
        if d <= 0.05:
            break
        seg = _reencode_segment(path, start, d, fps, scale)
        rng = f"{start:.1f}s-{min(start + d, dur):.1f}s"
        try:
            if expectation is not None:
                r = assert_visual(
                    seg,
                    f"{expectation} (This clip covers {rng} of a longer video; judge only this span.)",
                    model=model, endpoint=endpoint, video=True, native=native,
                    frames=server_frames)
                chunks.append({"range": rng, **r})
            else:
                a = look(seg, f"This clip covers {rng} of a longer video (the clip's 0:00 is "
                              f"{start:.1f}s absolute). {question} Report every time as an "
                              f"ABSOLUTE position in the full video by adding {start:.1f}s to "
                              f"clip-local times.",
                         model=model, endpoint=endpoint, video=True, native=native,
                         frames=server_frames)
                chunks.append({"range": rng, "answer": a})
        finally:
            try:
                os.remove(seg)
            except OSError:
                pass
    out = {"mode": "assert" if expectation is not None else "look", "fps": fps,
           "chunk_seconds": round(chunk_seconds, 2), "native": native,
           "duration_s": round(dur, 2), "chunks": chunks}
    if expectation is not None:
        failing = [c for c in chunks if not c.get("pass")]
        out["pass"] = not failing
        out["failing_ranges"] = [c["range"] for c in failing]
        out["reason"] = ("all chunks satisfied the expectation" if not failing else
                         "; ".join(f'[{c["range"]}] {c.get("reason", "")}' for c in failing)[:800])
    else:
        notes = "\n".join(f'[{c["range"]}] {c["answer"]}' for c in chunks)
        summary = chat([{"role": "user", "content": [{"type": "text", "text":
                        "These are sequential observations of one continuous video. Synthesize them "
                        "into a single coherent answer to the original question. Each observation's "
                        "[range] prefix is its ABSOLUTE span in the full video; treat any clip-local "
                        "times inside an observation as offset by that range's start. Cite absolute "
                        "times only. "
                        f"Original question: {question}\n\nObservations:\n{notes}"}]}],
                       model, endpoint)
        out["summary"] = summary
    return out


def health(endpoint=None) -> dict:
    endpoint = endpoint or DEFAULT_ENDPOINT
    url = endpoint.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- model recommender
ARCH_KEYWORDS = [
    ("blackwell", ["gb10", "gb200", "b200", "b100", "rtx pro 6000", "rtx 50", "5090", "5080", "5070", "blackwell"]),
    ("hopper", ["h100", "h200", "gh200", "hopper"]),
    ("ada", ["l40", "l4", "rtx 40", "4090", "4080", "4070", "ada", "rtx 6000 ada"]),
    ("ampere", ["a100", "a40", "a30", "a10", "rtx 30", "3090", "3080", "a6000", "ampere"]),
    ("turing", ["t4", "rtx 20", "2080", "2070", "turing"]),
]
# the efficient quant we recommend per arch (smallest footprint the hardware accelerates well)
ARCH_PREFERRED_QUANT = {"blackwell": "nvfp4", "hopper": "fp8", "ada": "fp8",
                        "ampere": "awq", "turing": "awq", "cpu": "gguf_q4"}


def _catalog() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def arch_from_name(name: str) -> str:
    low = (name or "").lower()
    for arch, kws in ARCH_KEYWORDS:
        if any(k in low for k in kws):
            return arch
    return "ampere"  # safe default: awq/bf16 path


def detect_gpu() -> dict:
    """Read local GPU name + total memory via nvidia-smi. vram_gb is None if unreadable (e.g. unified mem)."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {"gpu": None, "arch": None, "vram_gb": None, "error": "nvidia-smi not available"}
    name, _, mem = out[0].partition(",")
    name = name.strip()
    mem = mem.strip()
    vram_gb = None
    try:
        mib = float(mem)
        if mib > 0:
            vram_gb = round(mib / 1024.0, 1)
    except ValueError:
        pass  # "[N/A]" on unified-memory parts (e.g. GB10) - caller must supply --vram
    return {"gpu": name, "arch": arch_from_name(name), "vram_gb": vram_gb,
            "count": len(out), "all": out}


def recommend(vram_gb: float, arch: str, task: str, top: int = 4) -> dict:
    cat = _catalog()
    bytes_pp = cat["bytes_per_param"]
    overhead = cat["task_overhead_gb"].get(task, 3)
    quant = ARCH_PREFERRED_QUANT.get(arch, "awq")
    if quant not in bytes_pp:
        quant = "awq"
    budget = vram_gb * 0.9
    ranked = []
    for m in cat["models"]:
        tscore = float(m["tasks"].get(task, 0.15))
        if tscore <= 0:
            continue
        est = round(m["params_b"] * bytes_pp[quant] + overhead, 1)
        fits = est <= budget
        quality = min(m["params_b"], 16) / 16.0
        comfort = max(0.0, 1 - est / budget) if budget else 0.0
        score = 0.55 * tscore + 0.25 * quality + 0.20 * comfort
        if not fits:
            score *= 0.2  # keep as a fallback only
        ranked.append({
            "name": m["name"], "repo": m["repo"], "params_b": m["params_b"],
            "quant": quant, "est_vram_gb": est, "fits": fits,
            "serving": m["serving"], "license": m["license"], "note": m["note"],
            "task_score": round(tscore, 2), "score": round(score, 3),
        })
    ranked.sort(key=lambda r: r["score"], reverse=True)
    fitting = [r for r in ranked if r["fits"]]
    chosen = (fitting or ranked)[:top]
    return {
        "task": task, "arch": arch, "vram_gb": vram_gb, "quant": quant,
        "budget_gb": round(budget, 1),
        "note": None if fitting else "No catalog model fits this VRAM at the chosen quant; showing smallest options - reduce resolution/frames or use a bigger GPU.",
        "options": chosen,
    }


# --------------------------------------------------------------------------- MCP server
def run_mcp() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.exit("mcp SDK not available; run via `uv run` so PEP 723 deps install, or `pip install mcp`.")

    server = FastMCP("visual-qa")

    @server.tool()
    def look_tool(media: list[str], question: str, video: bool = False, frames: int = 8,
                  fps: float = 0, native: bool = False, model: str = "") -> str:
        """Describe / answer a question about UI screenshot(s) or a video. media = file paths.
        fps>0 samples at that rate (else `frames` evenly); native=True sends the video itself."""
        return look(media, question, model=model or None, video=video, frames=frames,
                    fps=fps or None, native=native)

    @server.tool()
    def assert_visual_tool(media: list[str], expectation: str, video: bool = False, frames: int = 8,
                           fps: float = 0, native: bool = False, model: str = "") -> dict:
        """Assert an expectation against UI screenshot(s)/video. Returns {pass, reason, evidence}.
        fps>0 samples at that rate; native=True sends the video itself (server-side sampling)."""
        return assert_visual(media, expectation, model=model or None, video=video, frames=frames,
                             fps=fps or None, native=native)

    @server.tool()
    def watch_tool(media: str, question: str = "", expectation: str = "", fps: float = 1.0,
                   chunk_seconds: float = 0, native: bool = True, scale: int = 0,
                   server_frames: int = 16, model: str = "") -> dict:
        """Watch a WHOLE video with full coverage: chunked at `fps` temporal resolution
        (1=overview, 15+=flicker hunting). Give exactly one of question (notes+summary) or
        expectation (all chunks must pass; failing time ranges reported)."""
        return watch(media, question=question or None, expectation=expectation or None,
                     fps=fps, chunk_seconds=chunk_seconds or None, native=native,
                     scale=scale or None, server_frames=server_frames, model=model or None)

    def _health_one(ep: str) -> dict:
        try:
            data = health(ep)
            return {"ok": True, "endpoint": ep,
                    "models": [m.get("id") for m in data.get("data", [])]}
        except Exception as e:  # noqa: BLE001 - any failure is a health failure
            return {"ok": False, "endpoint": ep, "error": str(e)}

    @server.tool()
    def health_tool(endpoint: str = "") -> dict:
        """Preflight the VLM serving: reachability + served models. With no endpoint, checks
        EVERY registered instance (plus the env default if unregistered). Call before long runs."""
        if endpoint:
            return _health_one(endpoint)
        reg = _registry()
        if not reg:
            return _health_one(DEFAULT_ENDPOINT)
        act = _active()
        out = {"active": act.get("name") if act else None,
               "instances": [{**{k: i.get(k) for k in ("name", "model", "tasks", "note")},
                              "active": bool(act and i.get("name") == act.get("name")),
                              **_health_one(i["endpoint"])} for i in reg]}
        out["ok"] = all(i["ok"] for i in out["instances"])
        return out

    @server.tool()
    def pick_model_tool(task: str = "ui-stills") -> dict:
        """ADVISORY: recommend a registered instance for a task
        (ui-stills|ocr|ui-video|physical-video). Does NOT switch routing - all calls keep
        using the active instance. To actually switch, call use_model_tool."""
        return pick(task)

    @server.tool()
    def use_model_tool(name: str) -> dict:
        """Select the ACTIVE instance that ALL look/assert/watch calls run on (persists in the
        registry). The instance must be serving (started); see serving start.sh/stop.sh."""
        return use(name)

    @server.tool()
    def recommend_models_tool(vram_gb: float, task: str = "ui-stills", arch: str = "", top: int = 4) -> dict:
        """Rank VLMs for a GPU (vram_gb, arch) + task (ui-stills|ocr|ui-video|physical-video). Present
        the returned options to the user to choose from before serving."""
        return recommend(vram_gb, arch or "ampere", task, top)

    server.run()


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="visual-qa VLM 'eyes' client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("mcp", help="run as an MCP (stdio) server")

    ph = sub.add_parser("health", help="check the endpoint is up and list models")
    ph.add_argument("--endpoint")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--media", action="append", required=True, help="image/video path (repeatable)")
    common.add_argument("--video", action="store_true", help="treat media as video, sample frames")
    common.add_argument("--frames", type=int, default=8)
    common.add_argument("--fps", type=float, default=None,
                        help="sample at this rate instead of a fixed frame count (mind the server image cap)")
    common.add_argument("--native", action="store_true",
                        help="tier 2: send the video itself (server samples with timestamps)")
    common.add_argument("--model", default=None)
    common.add_argument("--endpoint", default=None)

    pl = sub.add_parser("look", parents=[common], help="free-text answer about the media")
    pl.add_argument("--q", "--question", dest="question", required=True)

    pa = sub.add_parser("assert", parents=[common], help="assert an expectation; prints JSON")
    pa.add_argument("--expect", "--expectation", dest="expectation", required=True)

    pw = sub.add_parser("watch", help="tier 3: chunked whole-video watch at a target fps; prints JSON")
    pw.add_argument("--media", required=True, help="video path")
    pw.add_argument("--q", "--question", dest="question", default=None)
    pw.add_argument("--expect", "--expectation", dest="expectation", default=None)
    pw.add_argument("--fps", type=float, default=1.0, help="temporal resolution (1=overview, 15+=flicker)")
    pw.add_argument("--chunk-seconds", type=float, default=None, help="override auto chunk length")
    pw.add_argument("--images", action="store_true", help="use image-frames mode instead of native video")
    pw.add_argument("--scale", type=int, default=None, help="downscale to this height (speeds up judging)")
    pw.add_argument("--server-frames", type=int, default=16, help="server's per-video frame budget")
    pw.add_argument("--max-chunks", type=int, default=64)
    pw.add_argument("--model", default=None)
    pw.add_argument("--endpoint", default=None)

    pp = sub.add_parser("pick", help="ADVISORY: recommend an instance for a task (JSON); does not switch")
    pp.add_argument("--task", default="ui-stills",
                    choices=["ui-stills", "ocr", "ui-video", "physical-video"])

    pu = sub.add_parser("use", help="select the ACTIVE instance all calls run on (persists to registry)")
    pu.add_argument("name", help="registered instance name = slug of the model id (e.g. qwen3-vl-32b-instruct)")

    sub.add_parser("detect", help="detect local GPU name / arch / VRAM via nvidia-smi (JSON)")

    pr = sub.add_parser("recommend", help="rank catalog models for a GPU + task (JSON)")
    pr.add_argument("--vram", type=float, help="GPU memory in GB (required unless --detect supplies it)")
    pr.add_argument("--arch", help="GPU arch: blackwell|hopper|ada|ampere|turing|cpu (else inferred)")
    pr.add_argument("--task", default="ui-stills",
                    choices=["ui-stills", "ocr", "ui-video", "physical-video"])
    pr.add_argument("--top", type=int, default=4)
    pr.add_argument("--detect", action="store_true", help="read GPU from local nvidia-smi")

    args = p.parse_args(argv)

    if args.cmd == "mcp":
        run_mcp()
        return 0
    if args.cmd == "health":
        if args.endpoint:
            print(json.dumps(health(args.endpoint), indent=2))
            return 0
        reg = _registry()
        if not reg:
            print(json.dumps(health(None), indent=2))
            return 0
        out = []
        for inst in reg:
            try:
                models = [m.get("id") for m in health(inst["endpoint"]).get("data", [])]
                out.append({"name": inst.get("name"), "endpoint": inst["endpoint"],
                            "ok": True, "models": models})
            except Exception as e:  # noqa: BLE001
                out.append({"name": inst.get("name"), "endpoint": inst["endpoint"],
                            "ok": False, "error": str(e)})
        print(json.dumps(out, indent=2))
        return 0 if all(i["ok"] for i in out) else 1
    if args.cmd == "pick":
        print(json.dumps(pick(args.task), indent=2))
        return 0
    if args.cmd == "use":
        print(json.dumps(use(args.name), indent=2))
        return 0
    if args.cmd == "look":
        print(look(args.media, args.question, model=args.model, video=args.video,
                   frames=args.frames, endpoint=args.endpoint, fps=args.fps, native=args.native))
        return 0
    if args.cmd == "assert":
        result = assert_visual(args.media, args.expectation, model=args.model, video=args.video,
                               frames=args.frames, endpoint=args.endpoint,
                               fps=args.fps, native=args.native)
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1
    if args.cmd == "watch":
        result = watch(args.media, question=args.question, expectation=args.expectation,
                       fps=args.fps, chunk_seconds=args.chunk_seconds, native=not args.images,
                       scale=args.scale, server_frames=args.server_frames,
                       max_chunks=args.max_chunks, model=args.model, endpoint=args.endpoint)
        print(json.dumps(result, indent=2))
        return 0 if result.get("pass", True) else 1
    if args.cmd == "detect":
        print(json.dumps(detect_gpu(), indent=2))
        return 0
    if args.cmd == "recommend":
        vram, arch = args.vram, args.arch
        if args.detect:
            d = detect_gpu()
            vram = vram or d.get("vram_gb")
            arch = arch or d.get("arch")
        if not arch:
            arch = "ampere"
        if not vram:
            sys.exit("recommend: need --vram GB (nvidia-smi could not report it; unified-memory parts "
                     "like GB10 read N/A - pass the usable budget, e.g. --vram 110)")
        print(json.dumps(recommend(vram, arch, args.task, args.top), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
