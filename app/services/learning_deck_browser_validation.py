"""Browser validation for the exact Learning Deck viewer served to clients."""

from __future__ import annotations

import json
from string import Template
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.agent_vm_runtime import AgentVmSession
from app.services.learning_deck_artifacts import LearningDeckArtifactError
from app.services.learning_deck_layout import RESPONSIVE_LEARNING_DECK_LAYOUT
from app.services.learning_deck_viewer import with_learning_deck_navigation_controls

BROWSER_VALIDATION_RESULT_PREFIX = "NEWSLY_BROWSER_VALIDATION="
VALIDATION_VIEWER_PATH = "output/.newsly-viewer-validation.html"


class BrowserCanvasSize(BaseModel):
    """Integer browser viewport or Reveal canvas dimensions."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int


class BrowserRevealIndices(BaseModel):
    """Reveal horizontal, vertical, and fragment indices."""

    model_config = ConfigDict(extra="forbid")

    h: int
    v: int
    f: int


class BrowserOccupancyRange(BaseModel):
    """Observed fraction of slide height occupied by visible slide content."""

    model_config = ConfigDict(extra="forbid")

    minimum: float
    maximum: float


class BrowserOrientationResult(BaseModel):
    """Layout measurements for every slide in one device orientation."""

    model_config = ConfigDict(extra="forbid")

    viewport: BrowserCanvasSize
    canvas: BrowserCanvasSize
    slides_checked: int
    overflow_slides: list[str]
    vertical_occupancy: BrowserOccupancyRange


class BrowserValidationOutcome(BaseModel):
    """Typed contract emitted by the Playwright viewer validation script."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed"]
    validator: Literal["playwright_chromium"]
    responsive_layout: str
    reveal_ready: Literal[True]
    current_slide_exists: Literal[True]
    slide_count: int
    navigation: Literal[
        "next_previous_round_trip",
        "single_slide_fragment_round_trip",
        "single_slide_stable",
    ]
    initial_indices: BrowserRevealIndices
    next_indices: BrowserRevealIndices
    previous_indices: BrowserRevealIndices
    relevant_asset_loads: int
    portrait: BrowserOrientationResult
    landscape: BrowserOrientationResult


def validate_learning_deck_in_browser(
    sandbox: AgentVmSession,
    *,
    index_html: str,
) -> dict[str, Any]:
    """Render and validate the same viewer HTML returned by the hosting path."""
    unavailable = browser_validation_unavailable(sandbox)
    if unavailable is not None:
        return unavailable

    viewer_html = with_learning_deck_navigation_controls(index_html.encode()).decode()
    sandbox.write_file(VALIDATION_VIEWER_PATH, viewer_html)
    result = sandbox.execute_bash(_browser_validation_command(), timeout_seconds=30)

    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown browser error"
        raise LearningDeckArtifactError(f"Browser validation failed: {detail}")
    return parse_browser_validation_outcome(result.stdout)


def parse_browser_validation_outcome(stdout: str) -> dict[str, Any]:
    """Parse and enforce the typed browser-validation result contract."""
    payload = next(
        (
            line.removeprefix(BROWSER_VALIDATION_RESULT_PREFIX)
            for line in reversed(stdout.splitlines())
            if line.startswith(BROWSER_VALIDATION_RESULT_PREFIX)
        ),
        None,
    )
    if payload is None:
        raise LearningDeckArtifactError(
            "Browser validation failed: validator did not report a structured outcome"
        )
    try:
        raw_outcome = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LearningDeckArtifactError(
            "Browser validation failed: validator reported malformed JSON"
        ) from exc
    if not isinstance(raw_outcome, dict) or raw_outcome.get("status") != "passed":
        raise LearningDeckArtifactError(
            "Browser validation failed: validator did not report a passing outcome"
        )
    try:
        outcome = BrowserValidationOutcome.model_validate(raw_outcome)
    except ValidationError as exc:
        raise LearningDeckArtifactError(
            "Browser validation failed: validator reported an incomplete passing outcome"
        ) from exc

    expected_layout = RESPONSIVE_LEARNING_DECK_LAYOUT
    if (
        outcome.responsive_layout != expected_layout.version
        or outcome.slide_count < 1
        or outcome.portrait.canvas
        != BrowserCanvasSize(
            width=expected_layout.portrait.width,
            height=expected_layout.portrait.height,
        )
        or outcome.landscape.canvas
        != BrowserCanvasSize(
            width=expected_layout.landscape.width,
            height=expected_layout.landscape.height,
        )
        or outcome.portrait.slides_checked != outcome.slide_count
        or outcome.landscape.slides_checked != outcome.slide_count
        or outcome.portrait.overflow_slides
        or outcome.landscape.overflow_slides
    ):
        raise LearningDeckArtifactError(
            "Browser validation failed: validator reported an incomplete passing outcome"
        )
    return outcome.model_dump(mode="json")


def browser_validation_unavailable(sandbox: AgentVmSession) -> dict[str, Any] | None:
    """Return a structured skip when the VM cannot run browser validation."""
    capabilities = getattr(getattr(sandbox, "lease", None), "capabilities", None)
    if not isinstance(capabilities, dict):
        return {
            "status": "skipped",
            "reason": "sandbox_capabilities_not_reported",
            "missing_capabilities": ["chromium", "playwright"],
        }
    missing = sorted(
        capability for capability in ("chromium", "playwright") if not capabilities.get(capability)
    )
    if not missing:
        return None
    result: dict[str, Any] = {
        "status": "skipped",
        "reason": "sandbox_browser_capabilities_unavailable",
        "missing_capabilities": missing,
    }
    detail = capabilities.get("browser_validation_error")
    if detail:
        result["capability_error"] = str(detail)[:1000]
    return result


def _browser_validation_command() -> str:
    layout = RESPONSIVE_LEARNING_DECK_LAYOUT
    validation_config = {
        "path": VALIDATION_VIEWER_PATH,
        "layout": layout.version,
        "portrait": {
            "className": "newsly-learning-deck-portrait",
            "viewport": {"width": 390, "height": 844},
            "canvas": {"width": layout.portrait.width, "height": layout.portrait.height},
        },
        "landscape": {
            "className": "newsly-learning-deck-landscape",
            "viewport": {"width": 844, "height": 390},
            "canvas": {"width": layout.landscape.width, "height": layout.landscape.height},
        },
    }
    node_command = Template(_BROWSER_VALIDATION_COMMAND_TEMPLATE).substitute(
        validation_config=json.dumps(validation_config, separators=(",", ":"))
    )
    return f"trap 'rm -f -- {VALIDATION_VIEWER_PATH}' EXIT\n{node_command}"


_BROWSER_VALIDATION_COMMAND_TEMPLATE = r"""node - <<'NODE'
const { chromium } = require('playwright');
(async () => {
  let browser;
  try {
    const validationConfig = $validation_config;
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: validationConfig.portrait.viewport });
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(15000);

    const deckUrl = 'file://' + process.cwd() + '/' + validationConfig.path;
    const pageErrors = [];
    const failedRequests = [];
    const failedResponses = [];
    const relevantLoads = new Set();
    const isRelevantRequest = request => {
      const url = request.url();
      const isSyntheticUrl = ['data:', 'blob:', 'about:'].some(
        scheme => url.startsWith(scheme)
      );
      const isMainDeckDocument = request.isNavigationRequest() &&
        request.frame() === page.mainFrame() && url === deckUrl;
      return !isSyntheticUrl && !isMainDeckDocument;
    };

    page.on('pageerror', error => pageErrors.push(String(error)));
    page.on('requestfailed', request => {
      if (!isRelevantRequest(request)) return;
      failedRequests.push({
        url: request.url(),
        resource_type: request.resourceType(),
        error: request.failure()?.errorText || 'unknown request failure',
      });
    });
    page.on('response', response => {
      const request = response.request();
      if (!isRelevantRequest(request)) return;
      relevantLoads.add(response.url());
      if (response.status() >= 400) {
        failedResponses.push({
          url: response.url(),
          resource_type: request.resourceType(),
          status: response.status(),
        });
      }
    });

    await page.goto(deckUrl, { waitUntil: 'load' });
    await page.waitForFunction(() => Boolean(
      window.Reveal &&
      typeof window.Reveal.isReady === 'function' &&
      window.Reveal.isReady() &&
      typeof window.Reveal.getCurrentSlide === 'function' &&
      window.Reveal.getCurrentSlide()
    ));

    const responsiveLayout = await page.evaluate(() => {
      const marker = document.querySelector('meta[name="newsly-deck-layout"]');
      return marker ? marker.getAttribute('content') : null;
    });
    if (responsiveLayout !== validationConfig.layout) {
      throw new Error(JSON.stringify({
        reason: 'responsive layout metadata is missing',
        responsive_layout: responsiveLayout,
      }));
    }

    const waitForViewerLayout = spec => page.waitForFunction(expected => {
      const reveal = window.Reveal;
      if (!reveal || !reveal.isReady()) return false;
      const config = reveal.getConfig();
      return config.width === expected.canvas.width &&
        config.height === expected.canvas.height &&
        document.documentElement.classList.contains(expected.className);
    }, spec);

    const settleLayout = () => page.evaluate(() => new Promise(resolve => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
    }));

    const readDeckState = () => page.evaluate(() => {
      const reveal = window.Reveal;
      const currentSlide = reveal.getCurrentSlide();
      const indices = reveal.getIndices();
      return {
        current_slide_exists: Boolean(currentSlide),
        indices: { h: indices.h, v: indices.v, f: indices.f ?? -1 },
        slide_count: reveal.getTotalSlides(),
      };
    });

    const inspectCurrentSlide = () => page.evaluate(() => {
      const reveal = window.Reveal;
      const slide = reveal.getCurrentSlide();
      const indices = reveal.getIndices();
      const slideRect = slide.getBoundingClientRect();
      const visibleRects = Array.from(slide.querySelectorAll('*'))
        .filter(element => {
          const style = window.getComputedStyle(element);
          return style.display !== 'none' && style.visibility !== 'hidden' &&
            style.position !== 'fixed' && element.getClientRects().length > 0;
        })
        .map(element => element.getBoundingClientRect())
        .filter(rect => rect.width > 0 && rect.height > 0);
      const contentTop = visibleRects.length
        ? Math.min(...visibleRects.map(rect => rect.top))
        : slideRect.top;
      const contentBottom = visibleRects.length
        ? Math.max(...visibleRects.map(rect => rect.bottom))
        : slideRect.top;
      const tolerance = 2;
      const reasons = [];
      if (slide.scrollWidth > slide.clientWidth + tolerance) reasons.push('horizontal-scroll');
      if (slide.scrollHeight > slide.clientHeight + tolerance) reasons.push('vertical-scroll');
      if (visibleRects.some(rect => rect.left < slideRect.left - tolerance)) reasons.push('left');
      if (visibleRects.some(
        rect => rect.right > slideRect.right + tolerance
      )) reasons.push('right');
      if (visibleRects.some(rect => rect.top < slideRect.top - tolerance)) reasons.push('top');
      if (visibleRects.some(
        rect => rect.bottom > slideRect.bottom + tolerance
      )) reasons.push('bottom');
      return {
        key: slide.id || [indices.h, indices.v].join('.'),
        reasons: Array.from(new Set(reasons)),
        vertical_occupancy: slideRect.height > 0
          ? Math.max(0, contentBottom - contentTop) / slideRect.height
          : 0,
      };
    });

    const inspectOrientation = async spec => {
      await page.setViewportSize(spec.viewport);
      await waitForViewerLayout(spec);
      await settleLayout();
      const slideIndices = await page.evaluate(() => window.Reveal.getSlides().map(slide => {
        const indices = window.Reveal.getIndices(slide);
        return { h: indices.h, v: indices.v, f: -1 };
      }));
      const measurements = [];
      for (const indices of slideIndices) {
        await page.evaluate(target => window.Reveal.slide(target.h, target.v, target.f), indices);
        await page.waitForFunction(target => {
          const current = window.Reveal.getIndices();
          return current.h === target.h && current.v === target.v;
        }, indices);
        await settleLayout();
        measurements.push(await inspectCurrentSlide());
      }
      const occupancies = measurements.map(item => item.vertical_occupancy);
      return {
        viewport: spec.viewport,
        canvas: spec.canvas,
        slides_checked: measurements.length,
        overflow_slides: measurements
          .filter(item => item.reasons.length > 0)
          .map(item => item.key + ': ' + item.reasons.join(', ')),
        vertical_occupancy: {
          minimum: occupancies.length ? Math.min(...occupancies) : 0,
          maximum: occupancies.length ? Math.max(...occupancies) : 0,
        },
      };
    };

    const portrait = await inspectOrientation(validationConfig.portrait);
    await page.evaluate(() => window.Reveal.slide(0, 0, -1));
    await page.waitForFunction(() => {
      const indices = window.Reveal.getIndices();
      return indices.h === 0 && indices.v === 0;
    });
    const initial = await readDeckState();
    if (!initial.current_slide_exists || initial.slide_count < 1) {
      throw new Error(JSON.stringify({ reason: 'current slide unavailable', initial }));
    }

    const sameIndices = (left, right) =>
      left.h === right.h && left.v === right.v && left.f === right.f;
    await page.evaluate(() => window.Reveal.next());
    let afterNext;
    let afterPrevious;
    let navigation;
    if (initial.slide_count === 1) {
      await page.waitForTimeout(100);
      afterNext = await readDeckState();
      await page.evaluate(() => window.Reveal.prev());
      if (sameIndices(initial.indices, afterNext.indices)) {
        await page.waitForTimeout(100);
        afterPrevious = await readDeckState();
        if (!sameIndices(initial.indices, afterPrevious.indices)) {
          throw new Error(JSON.stringify({
            reason: 'single-slide navigation was not stable',
            initial,
            afterNext,
            afterPrevious,
          }));
        }
        navigation = 'single_slide_stable';
      } else {
        await page.waitForFunction(expected => {
          const current = window.Reveal.getIndices();
          return current.h === expected.h && current.v === expected.v &&
            (current.f ?? -1) === expected.f;
        }, initial.indices);
        afterPrevious = await readDeckState();
        navigation = 'single_slide_fragment_round_trip';
      }
    } else {
      await page.waitForFunction(previous => {
        const current = window.Reveal.getIndices();
        const fragment = current.f ?? -1;
        return current.h !== previous.h || current.v !== previous.v ||
          fragment !== previous.f;
      }, initial.indices);
      afterNext = await readDeckState();
      await page.evaluate(() => window.Reveal.prev());
      await page.waitForFunction(expected => {
        const current = window.Reveal.getIndices();
        return current.h === expected.h && current.v === expected.v &&
          (current.f ?? -1) === expected.f;
      }, initial.indices);
      afterPrevious = await readDeckState();
      navigation = 'next_previous_round_trip';
    }

    const landscape = await inspectOrientation(validationConfig.landscape);
    await page.waitForTimeout(250);
    if (pageErrors.length || failedRequests.length || failedResponses.length) {
      throw new Error(JSON.stringify({
        reason: 'browser runtime or asset load failure',
        page_errors: pageErrors,
        failed_requests: failedRequests,
        failed_responses: failedResponses,
      }));
    }
    if (portrait.overflow_slides.length || landscape.overflow_slides.length) {
      throw new Error(JSON.stringify({
        reason: 'slide content overflows the hosted viewer canvas',
        portrait_overflow: portrait.overflow_slides,
        landscape_overflow: landscape.overflow_slides,
      }));
    }

    const outcome = {
      status: 'passed',
      validator: 'playwright_chromium',
      responsive_layout: responsiveLayout,
      reveal_ready: true,
      current_slide_exists: initial.current_slide_exists,
      slide_count: initial.slide_count,
      navigation,
      initial_indices: initial.indices,
      next_indices: afterNext.indices,
      previous_indices: afterPrevious.indices,
      relevant_asset_loads: relevantLoads.size,
      portrait,
      landscape,
    };
    console.log('NEWSLY_BROWSER_VALIDATION=' + JSON.stringify(outcome));
  } finally {
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
NODE"""
