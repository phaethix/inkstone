"""core.density — zero-cost, zero-API density / cost / duration estimator (D1).

Before spending a single token on image generation, users want to know how many
panels a novel will become, how many pages that is, what it costs, and how long
it will take. This module answers those questions **purely locally** — it never
touches the network — so the estimate is instant and free.

Design notes for the next milestones:
- ``DensityPlan`` is intentionally tiny and stable so D2 can serialize it into
  ``ProjectState.density`` and fold ``tier`` / ``panels_per_chunk`` into the
  input fingerprint without a schema change.
- ``DensityEstimate`` carries a few *hook* fields (``tier``, ``panels_per_chunk``,
  ``description``, ``output_format``, ``output_name``) that D2 will reuse; this
  milestone does not compute the fingerprint itself.

The panel-per-chunk numbers (A/B/C) and the per-page / per-minute rates are
**experience values** awaiting calibration against a real 《三体》 sample.
"""

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Literal

from core.comic.segmentation import segment_text

# --------------------------------------------------------------------------- #
# Experience constants (to be calibrated against a real 《三体》 sample)
# --------------------------------------------------------------------------- #

# Panels planned per source-text chunk for each density tier.
PANELS_PER_CHUNK_A = 14  # 主线完备：每 chunk 拆出最多格，叙事最完整
PANELS_PER_CHUNK_B = 8  # 标准密度
PANELS_PER_CHUNK_C = 3  # 极简示意：每 chunk 仅关键格

# Throughput used only for wall-clock estimation (panels generated per minute,
# per worker). Experience value — real numbers depend on the t2i endpoint.
PANELS_PER_MINUTE = 2

# Pages produced per PDF page in ``page`` mode. Kept in sync with
# ``LayoutEngine._paginate(per_page=4)`` so the page estimate matches the real
# layout engine instead of drifting.
PANELS_PER_PAGE = 4

# Webtoon vertical-strip size guard. Estimated MB per page; warn when the
# projected webtoon exceeds the threshold. Both are experience values and the
# threshold is overridable via the ``INKSTONE_WEBTOON_WARN_MB`` env var.
WEBTOON_WARN_MB = 50.0
EST_PAGE_MB = 0.3
ENV_WEBTOON_WARN_MB = "INKSTONE_WEBTOON_WARN_MB"

# Files below this character count are too small for a meaningful estimate;
# the estimator still returns numbers but attaches an advisory warning.
_TINY_FILE_CHARS = 100

DensityTier = Literal["A", "B", "C"]


@dataclass(frozen=True)
class DensityPlan:
    """The panel-density recipe for one tier.

    Frozen so it is trivially hashable / serializable into ``state.density``
    later (D2). ``description`` is the human-readable tier name shown in CLI.
    """

    tier: DensityTier
    panels_per_chunk: int
    description: str


@dataclass
class DensityEstimate:
    """Structured result of :func:`estimate`.

    The eight fields named in the D1 contract are listed first (no defaults);
    the remaining fields are *hook* fields that D2 will reuse (density tier,
    serialized plan bits, output target) without changing this schema.
    """

    total_chars: int
    chunks: int
    panels: int
    pages: int
    estimated_minutes: int
    cost_label: str
    webtoon_warning: bool
    warnings: list[str]
    # --- hook fields for D2 (state.density / fingerprint) ---
    tier: DensityTier = "B"
    panels_per_chunk: int = PANELS_PER_CHUNK_B
    description: str = ""
    output_format: str = "page"
    output_name: str = "comic.pdf"


def get_density_plan(tier: DensityTier) -> DensityPlan:
    """Return the experience-tuned panel-density plan for ``tier``.

    Args:
        tier: one of ``"A"`` (主线完备), ``"B"`` (标准), ``"C"`` (极简).
    """
    mapping: dict[DensityTier, tuple[str, int]] = {
        "A": ("主线完备", PANELS_PER_CHUNK_A),
        "B": ("标准密度", PANELS_PER_CHUNK_B),
        "C": ("极简示意", PANELS_PER_CHUNK_C),
    }
    description, panels_per_chunk = mapping[tier]
    return DensityPlan(tier=tier, panels_per_chunk=panels_per_chunk, description=description)


def _read_text(book_path: str | Path) -> str:
    """Read the novel text, normalizing line endings like the pipeline does."""
    return Path(book_path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _cost_label(api: str, panels: int, price_per_panel: float | None) -> str:
    """Build the human-facing cost string for the chosen billing backend."""
    if api == "agnes":
        # Agnes free tier is the default; the R6 quota policy is still in flux,
        # so we surface the "变动风险" caveat up front instead of promising ¥0.
        return "¥0，依赖 R6 额度政策，有变动风险"
    if api == "openai-compat":
        if price_per_panel is None:
            return "按您的 endpoint 单价"
        return f"约 ¥{panels * price_per_panel:.2f}"
    # Unknown backend: stay conservative and treat as free, but say so.
    return "¥0（未知 API 档，已按免费档处理）"


def _webtoon_warning(output_format: str, pages: int) -> tuple[bool, str | None]:
    """Return ``(triggered, message)`` for the webtoon size guard.

    Only ``webtoon`` output is at risk of an unwieldy single-PNG strip, so the
    guard is a no-op for ``page`` mode. The threshold is read from the env var
    ``INKSTONE_WEBTOON_WARN_MB`` (default :data:`WEBTOON_WARN_MB`).
    """
    if output_format != "webtoon":
        return False, None
    threshold_mb = float(_read_webtoon_threshold())
    est_mb = pages * EST_PAGE_MB
    if est_mb > threshold_mb:
        return True, f"检测到 webtoon 可能超 {est_mb:.0f} MB，建议改用 --format page"
    return False, None


def _read_webtoon_threshold() -> float:
    raw = __import__("os").environ.get(ENV_WEBTOON_WARN_MB)
    if raw is None or raw == "":
        return WEBTOON_WARN_MB
    try:
        return float(raw)
    except ValueError:
        return WEBTOON_WARN_MB


def _output_name(output_format: str) -> str:
    """Filename produced for the chosen format (matches the pipeline's output)."""
    return "webtoon.png" if output_format == "webtoon" else "comic.pdf"


def estimate(
    book_path: str | Path,
    density: DensityTier = "B",
    output_format: str = "page",
    api: str = "agnes",
    concurrency: int = 4,
    price_per_panel: float | None = None,
    asset_dir: str | None = None,
) -> DensityEstimate:
    """Estimate density, cost, and duration for a novel **without any API call**.

    Pure local estimation: the text is chunked with the same ``segment_text``
    helper the real pipeline uses (no network), then scaled by the tier's
    panels-per-chunk coefficient to project total panels and pages.

    Args:
        book_path: path to the source ``.txt`` novel.
        density: density tier ``"A"`` / ``"B"`` / ``"C"`` (default ``"B"``).
        output_format: ``"page"`` (PDF) or ``"webtoon"`` (vertical PNG).
        api: billing backend ``"agnes"`` (free quota) or ``"openai-compat"``.
        concurrency: parallel panel workers assumed for the duration estimate.
        price_per_panel: ¥ per panel for ``openai-compat``; ``None`` → "按单价".
        asset_dir: reserved hook for D2 (asset/output location); unused now.

    Returns:
        A :class:`DensityEstimate` with all projected counts, the cost string,
        the webtoon guard flag, and any advisory warnings.
    """
    # NOTE: the public param is named ``output_format`` (not ``format``) to avoid
    # shadowing the builtin and to stay consistent with core.pipelines.* APIs.
    # Reject a negative per-panel price up front so we never print a "约 ¥-1.60"
    # style string (explicit reject, per QA review).
    if price_per_panel is not None and price_per_panel < 0:
        raise ValueError("price_per_panel must be non-negative")
    text = _read_text(book_path)
    total_chars = len(text)

    # Local chunking — mirrors the real pipeline, no API, no cost.
    chunks = len(segment_text(text))

    plan = get_density_plan(density)
    panels_per_chunk = plan.panels_per_chunk
    panels = chunks * panels_per_chunk
    pages = ceil(panels / PANELS_PER_PAGE)

    # Wall-clock estimate. Guard concurrency so a bad input can't divide by zero.
    effective_concurrency = max(1, int(concurrency))
    estimated_minutes = ceil(panels / (effective_concurrency * PANELS_PER_MINUTE))

    cost_label = _cost_label(api, panels, price_per_panel)
    webtoon_warning, webtoon_msg = _webtoon_warning(output_format, pages)

    warnings: list[str] = []
    if total_chars == 0:
        warnings.append("源文件为空（0 字符），预估不可靠")
    elif total_chars < _TINY_FILE_CHARS:
        warnings.append(
            f"源文件过小（{total_chars} 字符），仅 {chunks} 段，预估仅供参考"
        )
    if webtoon_msg:
        warnings.append(webtoon_msg)

    return DensityEstimate(
        total_chars=total_chars,
        chunks=chunks,
        panels=panels,
        pages=pages,
        estimated_minutes=estimated_minutes,
        cost_label=cost_label,
        webtoon_warning=webtoon_warning,
        warnings=warnings,
        tier=density,
        panels_per_chunk=panels_per_chunk,
        description=plan.description,
        output_format=output_format,
        output_name=_output_name(output_format),
    )
