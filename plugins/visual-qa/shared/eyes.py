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


def _sample_video(path: str, frames: int) -> list[str]:
    """Sample `frames` evenly-spaced frames from a video using ffmpeg into a temp dir."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found; install it or pass extracted frames as --media images")
    out_dir = tempfile.mkdtemp(prefix="eyes_frames_")
    # thumbnail-style even sampling: select N frames across the whole clip
    pattern = os.path.join(out_dir, "f_%03d.png")
    cmd = [
        "ffmpeg", "-loglevel", "error", "-i", path,
        "-vf", f"thumbnail,fps=1",  # baseline; refined below if duration known
        "-frames:v", str(frames), "-vsync", "vfr", pattern,
    ]
    subprocess.run(cmd, check=True)
    out = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.endswith(".png"))
    if not out:
        raise RuntimeError(f"ffmpeg produced no frames from {path}")
    return out


def _build_content(media: list[str], question: str, video: bool, frames: int) -> list[dict]:
    parts: list[dict] = []
    images: list[str] = []
    for m in media:
        if video or _looks_like_video(m):
            images.extend(_sample_video(m, frames))
        else:
            images.append(m)
    for img in images:
        parts.append({"type": "image_url", "image_url": {"url": _img_data_url(img)}})
    parts.append({"type": "text", "text": question})
    return parts


def _looks_like_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {".mov", ".mp4", ".webm", ".mkv", ".avi", ".gif"}


def chat(messages: list[dict], model: str, endpoint: str, max_tokens: int = 1024) -> str:
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
    return data["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict:
    """Pull the first balanced {...} object out of a model response."""
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


def look(media, question, *, model=None, video=False, frames=8, endpoint=None) -> str:
    media = [media] if isinstance(media, str) else list(media)
    endpoint = endpoint or DEFAULT_ENDPOINT
    model = model or (DEFAULT_VIDEO_MODEL if video else DEFAULT_MODEL)
    content = _build_content(media, question, video, frames)
    return chat([{"role": "user", "content": content}], model, endpoint)


def assert_visual(media, expectation, *, model=None, video=False, frames=8, endpoint=None) -> dict:
    media = [media] if isinstance(media, str) else list(media)
    endpoint = endpoint or DEFAULT_ENDPOINT
    model = model or (DEFAULT_VIDEO_MODEL if video else DEFAULT_MODEL)
    content = _build_content(media, f"Expectation to verify: {expectation}", video, frames)
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
    def look_tool(media: list[str], question: str, video: bool = False, frames: int = 8, model: str = "") -> str:
        """Describe / answer a question about UI screenshot(s) or a video. media = file paths."""
        return look(media, question, model=model or None, video=video, frames=frames)

    @server.tool()
    def assert_visual_tool(media: list[str], expectation: str, video: bool = False, frames: int = 8, model: str = "") -> dict:
        """Assert an expectation against UI screenshot(s)/video. Returns {pass, reason, evidence}."""
        return assert_visual(media, expectation, model=model or None, video=video, frames=frames)

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
    common.add_argument("--model", default=None)
    common.add_argument("--endpoint", default=None)

    pl = sub.add_parser("look", parents=[common], help="free-text answer about the media")
    pl.add_argument("--q", "--question", dest="question", required=True)

    pa = sub.add_parser("assert", parents=[common], help="assert an expectation; prints JSON")
    pa.add_argument("--expect", "--expectation", dest="expectation", required=True)

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
        print(json.dumps(health(args.endpoint), indent=2))
        return 0
    if args.cmd == "look":
        print(look(args.media, args.question, model=args.model, video=args.video,
                   frames=args.frames, endpoint=args.endpoint))
        return 0
    if args.cmd == "assert":
        result = assert_visual(args.media, args.expectation, model=args.model, video=args.video,
                               frames=args.frames, endpoint=args.endpoint)
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1
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
