use std::collections::BTreeMap;
use std::time::Duration;

use newsly_e2b::{
    CommandRequest, DirectE2bProvider, ExecutionTag, OutputLimits, SandboxHandle, SandboxUser,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::task_tools::{TaskFileError, TaskToolExecutor};

const VALIDATION_VIEWER_PATH: &str = "output/.newsly-viewer-validation.html";
const VALIDATION_SCRIPT_PATH: &str = "input/.newsly-browser-validation.cjs";
const RESULT_PREFIX: &str = "NEWSLY_BROWSER_VALIDATION=";
const FAILURE_PREFIX: &str = "NEWSLY_BROWSER_VALIDATION_FAILURE=";
const MINIMUM_BROWSER_BUDGET: Duration = Duration::from_secs(30);
const RESPONSIVE_LAYOUT: &str = "responsive-v2";

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CanvasSize {
    width: i64,
    height: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RevealIndices {
    h: i64,
    v: i64,
    f: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct OccupancyRange {
    minimum: f64,
    maximum: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct OrientationResult {
    viewport: CanvasSize,
    canvas: CanvasSize,
    slides_checked: i64,
    overflow_slides: Vec<String>,
    vertical_occupancy: OccupancyRange,
    #[serde(default)]
    overflow_details: Vec<Value>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BrowserValidationOutcome {
    status: String,
    validator: String,
    responsive_layout: String,
    reveal_ready: bool,
    current_slide_exists: bool,
    slide_count: i64,
    navigation: String,
    initial_indices: RevealIndices,
    next_indices: RevealIndices,
    previous_indices: RevealIndices,
    relevant_asset_loads: i64,
    portrait: OrientationResult,
    landscape: OrientationResult,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BrowserFailureReport {
    phase: String,
    reason: String,
    repairable: bool,
    #[serde(default)]
    context: Map<String, Value>,
}

fn parse_failure_report(raw: Value) -> Result<BrowserFailureReport, BrowserValidationError> {
    let report: BrowserFailureReport = serde_json::from_value(raw)?;
    if report.phase.trim().is_empty() || report.reason.trim().is_empty() {
        return Err(BrowserValidationError::InvalidOutcome(
            "failure report has an empty phase or reason".to_owned(),
        ));
    }
    if report.repairable
        && !matches!(
            report.phase.as_str(),
            "asset_load" | "readiness" | "navigation" | "canvas" | "layout_overflow"
        )
    {
        return Err(BrowserValidationError::InvalidOutcome(
            "failure report marks an infrastructure phase repairable".to_owned(),
        ));
    }
    Ok(report)
}

fn browser_deadline(deadline: Instant, now: Instant) -> Result<Instant, BrowserValidationError> {
    if deadline.saturating_duration_since(now) < MINIMUM_BROWSER_BUDGET {
        return Err(BrowserValidationError::Deadline);
    }
    Ok(deadline.min(now + Duration::from_secs(45)))
}

pub(super) async fn validate_in_browser(
    provider: &DirectE2bProvider,
    sandbox: &SandboxHandle,
    tools: &TaskToolExecutor,
    workspace_path: &str,
    index_html: &str,
    deadline: Instant,
    cancellation: CancellationToken,
) -> Result<Map<String, Value>, BrowserValidationError> {
    browser_deadline(deadline, Instant::now())?;
    tools
        .write_bytes_typed(VALIDATION_VIEWER_PATH, index_html.as_bytes().to_vec())
        .await
        .map_err(BrowserValidationError::Io)?;
    tools
        .write_bytes_typed(
            VALIDATION_SCRIPT_PATH,
            VALIDATION_SCRIPT.as_bytes().to_vec(),
        )
        .await
        .map_err(BrowserValidationError::Io)?;

    seal_validation_workspace(
        provider,
        sandbox,
        workspace_path,
        deadline,
        // A completed command stream cancels its token when dropped.
        cancellation.child_token(),
    )
    .await?;
    let absolute_deadline = browser_deadline(deadline, Instant::now())?;
    let user = SandboxUser::parse("user")?;
    let stream = provider
        .start_process(
            sandbox,
            CommandRequest {
                command: "/usr/bin/node".to_owned(),
                args: vec![VALIDATION_SCRIPT_PATH.to_owned()],
                env: BTreeMap::new(),
                cwd: Some(workspace_path.to_owned()),
                username: Some(user),
                tag: ExecutionTag::new(),
                stdin_enabled: false,
                absolute_deadline,
                idle_timeout: Duration::from_secs(30),
                output_limits: OutputLimits {
                    stdout_bytes: 200_000,
                    stderr_bytes: 200_000,
                    combined_bytes: 300_000,
                    event_bytes: 220_000,
                    channel_capacity: 32,
                },
            },
            cancellation,
        )
        .await?;
    let result = stream.collect_result().await?;
    if result.exit_code != 0 {
        let combined = format!("{}\n{}", result.output.stdout, result.output.stderr);
        if let Some(report) = prefixed_json(&combined, FAILURE_PREFIX) {
            let report = parse_failure_report(report)?;
            let message = report.reason.chars().take(4_000).collect();
            let repairable = report.repairable;
            return Err(BrowserValidationError::Validation {
                message,
                report: serde_json::to_value(report)?
                    .as_object()
                    .cloned()
                    .ok_or_else(|| {
                        BrowserValidationError::InvalidOutcome(
                            "failure report was not an object".to_owned(),
                        )
                    })?,
                repairable,
            });
        }
        let detail = [result.output.stderr.trim(), result.output.stdout.trim()]
            .into_iter()
            .find(|value| !value.is_empty())
            .unwrap_or("unknown browser error");
        return Err(BrowserValidationError::Validation {
            message: detail.chars().take(4_000).collect(),
            report: Map::from_iter([
                ("phase".to_owned(), Value::from("validator_process")),
                (
                    "reason".to_owned(),
                    Value::from(detail.chars().take(4_000).collect::<String>()),
                ),
                ("repairable".to_owned(), Value::Bool(false)),
            ]),
            repairable: false,
        });
    }
    let raw = prefixed_json(&result.output.stdout, RESULT_PREFIX).ok_or_else(|| {
        BrowserValidationError::Validation {
            message: "validator did not report a structured outcome".to_owned(),
            report: Map::new(),
            repairable: false,
        }
    })?;
    let outcome: BrowserValidationOutcome = serde_json::from_value(raw)?;
    validate_outcome(&outcome)?;
    serde_json::to_value(outcome)?
        .as_object()
        .cloned()
        .ok_or_else(|| {
            BrowserValidationError::InvalidOutcome("outcome was not an object".to_owned())
        })
}

async fn seal_validation_workspace(
    provider: &DirectE2bProvider,
    sandbox: &SandboxHandle,
    workspace_path: &str,
    deadline: Instant,
    cancellation: CancellationToken,
) -> Result<(), BrowserValidationError> {
    let absolute_deadline = browser_deadline(deadline, Instant::now())?;
    let result = provider
        .start_process(
            sandbox,
            CommandRequest {
                command: "/bin/chmod".to_owned(),
                args: vec![
                    "-R".to_owned(),
                    "a+rX,a-w".to_owned(),
                    "--".to_owned(),
                    workspace_path.to_owned(),
                ],
                env: BTreeMap::new(),
                cwd: None,
                username: Some(SandboxUser::parse("root")?),
                tag: ExecutionTag::new(),
                stdin_enabled: false,
                absolute_deadline,
                idle_timeout: Duration::from_secs(30),
                output_limits: OutputLimits {
                    stdout_bytes: 10_000,
                    stderr_bytes: 10_000,
                    combined_bytes: 20_000,
                    event_bytes: 22_000,
                    channel_capacity: 8,
                },
            },
            cancellation,
        )
        .await?
        .collect_result()
        .await?;
    if result.exit_code != 0 {
        return Err(BrowserValidationError::Validation {
            message: "could not seal the browser validation snapshot".to_owned(),
            report: Map::from_iter([
                ("phase".to_owned(), Value::from("snapshot_seal")),
                (
                    "reason".to_owned(),
                    Value::from("could not seal the browser validation snapshot"),
                ),
                ("repairable".to_owned(), Value::Bool(false)),
            ]),
            repairable: false,
        });
    }
    Ok(())
}

fn validate_outcome(outcome: &BrowserValidationOutcome) -> Result<(), BrowserValidationError> {
    let initial = &outcome.initial_indices;
    let next = &outcome.next_indices;
    let valid_indices =
        |indices: &RevealIndices| indices.h >= 0 && indices.v >= 0 && indices.f >= -1;
    let navigation = valid_indices(initial)
        && valid_indices(next)
        && outcome.previous_indices == *initial
        && match outcome.navigation.as_str() {
            "next_previous_round_trip" => outcome.slide_count > 1 && next != initial,
            "single_slide_fragment_round_trip" => {
                outcome.slide_count == 1
                    && next.h == initial.h
                    && next.v == initial.v
                    && next.f > initial.f
            }
            "single_slide_stable" => outcome.slide_count == 1 && next == initial,
            _ => false,
        };
    let valid_orientation = |orientation: &OrientationResult, width, height| {
        let occupancy = &orientation.vertical_occupancy;
        orientation.viewport.width == width
            && orientation.viewport.height == height
            && orientation.overflow_details.is_empty()
            && occupancy.minimum.is_finite()
            && occupancy.maximum.is_finite()
            && occupancy.minimum >= 0.0
            && occupancy.maximum >= occupancy.minimum
    };
    let complete = outcome.status == "passed"
        && outcome.validator == "playwright_chromium"
        && outcome.responsive_layout == RESPONSIVE_LAYOUT
        && outcome.reveal_ready
        && outcome.current_slide_exists
        && outcome.slide_count >= 1
        && navigation
        && outcome.relevant_asset_loads >= 0
        && valid_orientation(&outcome.portrait, 390, 844)
        && valid_orientation(&outcome.landscape, 844, 390)
        && outcome.portrait.canvas.width == 720
        && outcome.portrait.canvas.height == 1280
        && outcome.landscape.canvas.width == 1280
        && outcome.landscape.canvas.height == 720
        && outcome.portrait.slides_checked == outcome.slide_count
        && outcome.landscape.slides_checked == outcome.slide_count
        && outcome.portrait.overflow_slides.is_empty()
        && outcome.landscape.overflow_slides.is_empty();
    if complete {
        Ok(())
    } else {
        Err(BrowserValidationError::InvalidOutcome(
            "validator reported an incomplete passing outcome".to_owned(),
        ))
    }
}

fn prefixed_json(output: &str, prefix: &str) -> Option<Value> {
    output
        .lines()
        .rev()
        .find_map(|line| line.strip_prefix(prefix))
        .and_then(|payload| serde_json::from_str(payload).ok())
}

pub(super) fn hosted_viewer_html(index_html: &str) -> String {
    const SHELL: &str = r#"
<style data-newsly-learning-deck-controls="style">
html,body{width:100%;height:100%;margin:0;overflow:hidden;overscroll-behavior:none}
.reveal{width:100vw!important;height:100vh!important;min-height:100vh!important;overflow:hidden!important}
.reveal .slides section{box-sizing:border-box;max-width:100%}.reveal .slides section>*{max-width:100%}
.reveal img,.reveal svg,.reveal video,.reveal canvas{max-width:100%;height:auto}
</style>
<script data-newsly-learning-deck-controls="script">
(function(){
const profiles={phoneBreakpoint:700,desktop:{width:1280,height:720,margin:.025},responsive:{portrait:{width:720,height:1280,margin:.005},landscape:{width:1280,height:720,margin:.012}}};
function withReveal(callback){if(window.Reveal&&typeof window.Reveal.configure==='function'){callback(window.Reveal);return}setTimeout(()=>withReveal(callback),50)}
withReveal(reveal=>{function fit(){const portrait=matchMedia('(orientation: portrait)').matches;const phone=Math.min(screen.width||innerWidth,screen.height||innerHeight)<profiles.phoneBreakpoint;const canvas=phone?profiles.responsive[portrait?'portrait':'landscape']:profiles.desktop;document.documentElement.classList.toggle('newsly-learning-deck-portrait',phone&&portrait);document.documentElement.classList.toggle('newsly-learning-deck-landscape',phone&&!portrait);document.documentElement.classList.add('newsly-learning-deck-responsive');return {controls:true,progress:false,width:canvas.width,height:canvas.height,margin:canvas.margin,center:false,minScale:.05,maxScale:3,view:'slide',scrollActivationWidth:null}}
function apply(){reveal.configure(fit());if(typeof reveal.toggleScrollView==='function')reveal.toggleScrollView(false)}
apply();reveal.on&&reveal.on('ready',apply);setTimeout(apply,100);setTimeout(apply,500);addEventListener('resize',apply)
})})();
</script>
"#;
    let lower = index_html.to_ascii_lowercase();
    if let Some(index) = lower.rfind("</body") {
        format!(
            "{}\n{SHELL}\n{}",
            &index_html[..index],
            &index_html[index..]
        )
    } else {
        format!("{index_html}\n{SHELL}")
    }
}

const VALIDATION_SCRIPT: &str = r"
const { chromium, errors: { TimeoutError } } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const RESULT = 'NEWSLY_BROWSER_VALIDATION=';
const FAILURE = 'NEWSLY_BROWSER_VALIDATION_FAILURE=';
const specs = {
  portrait: { viewport: { width: 390, height: 844 }, canvas: { width: 720, height: 1280 } },
  landscape: { viewport: { width: 844, height: 390 }, canvas: { width: 1280, height: 720 } },
};
(async()=>{
  let browser; let phase='browser_launch';
  const artifactFailure=(reason,phase,context)=>Object.assign(new Error(reason),{artifactFailure:true,phase,context});
  try {
    browser = await chromium.launch({headless:true});
    const url = pathToFileURL(path.resolve('output/.newsly-viewer-validation.html')).href;
    let shared;
    let relevantLoads = new Set();
    for (const [name,spec] of Object.entries(specs)) {
      const page = await browser.newPage({viewport:spec.viewport});
      page.setDefaultTimeout(15000);
      const pageErrors=[]; const failed=[];
      page.on('pageerror',error=>pageErrors.push(String(error)));
      page.on('requestfailed',request=>failed.push({url:request.url(),error:request.failure()?.errorText||'request failed'}));
      page.on('response',response=>{if(response.url()!==url)relevantLoads.add(response.url());if(response.status()>=400)failed.push({url:response.url(),status:response.status()})});
      phase='asset_load';
      await page.goto(url,{waitUntil:'load'});
      phase='readiness';
      await page.waitForFunction(()=>window.Reveal&&typeof Reveal.isReady==='function'&&Reveal.isReady());
      await page.waitForTimeout(750);
      if(pageErrors.length||failed.length) throw artifactFailure('deck emitted browser or asset errors','asset_load',{pageErrors,failed});
      phase='measurement';
      const measured = await page.evaluate(async ({name,spec})=>{
        const reveal=window.Reveal; const slides=reveal.getSlides(); const overflow=[]; const overflowDetails=[]; const occupancy=[];
        const normalizeIndices=value=>({h:value?.h??0,v:value?.v??0,f:value?.f??-1});
        for(let i=0;i<slides.length;i++){
          const slide=slides[i]; const slideIndices=reveal.getIndices(slide); reveal.slide(slideIndices.h,slideIndices.v); await new Promise(r=>setTimeout(r,30));
          const current=reveal.getCurrentSlide(); const rect=current.getBoundingClientRect();
          const children=Array.from(current.children).filter(node=>getComputedStyle(node).display!=='none');
          const visible=children.map(node=>node.getBoundingClientRect()).filter(box=>box.width>0&&box.height>0);
          const top=visible.length?Math.min(...visible.map(box=>box.top)):rect.top;
          const bottom=visible.length?Math.max(...visible.map(box=>box.bottom)):rect.top;
          occupancy.push(Math.max(0,(bottom-top)/Math.max(rect.height,1)));
          const spills=current.scrollWidth>current.clientWidth+3||current.scrollHeight>current.clientHeight+3||visible.some(box=>box.left<rect.left-3||box.right>rect.right+3||box.top<rect.top-3||box.bottom>rect.bottom+3);
          if(spills){
            const slideId=current.id||`slide-${i+1}`; overflow.push(slideId);
            const bounds=box=>({left:box.left,top:box.top,right:box.right,bottom:box.bottom,width:box.width,height:box.height});
            if(current.scrollWidth>current.clientWidth+3||current.scrollHeight>current.clientHeight+3)overflowDetails.push({slide:slideId,element:'section',canvas:bounds(rect),scroll:{width:current.scrollWidth,height:current.scrollHeight},client:{width:current.clientWidth,height:current.clientHeight},excess_pixels:{horizontal:Math.max(0,current.scrollWidth-current.clientWidth),vertical:Math.max(0,current.scrollHeight-current.clientHeight)}});
            for(const [index,node] of children.entries()){
              const box=node.getBoundingClientRect();
              if(box.width>0&&box.height>0&&(box.left<rect.left-3||box.right>rect.right+3||box.top<rect.top-3||box.bottom>rect.bottom+3))overflowDetails.push({slide:slideId,element:node.tagName.toLowerCase()+(node.id?'#'+node.id:'')+` child-${index+1}`,canvas:bounds(rect),bounds:bounds(box),excess_pixels:{left:Math.max(0,rect.left-box.left),right:Math.max(0,box.right-rect.right),top:Math.max(0,rect.top-box.top),bottom:Math.max(0,box.bottom-rect.bottom)}});
            }
          }
        }
        reveal.slide(0,0,-1); await new Promise(r=>setTimeout(r,50));
        const initial=normalizeIndices(reveal.getIndices()); const count=slides.length; let next=initial; let previous=initial; let navigation='single_slide_stable';
        if(count>1){reveal.next();await new Promise(r=>setTimeout(r,80));next=normalizeIndices(reveal.getIndices());reveal.prev();await new Promise(r=>setTimeout(r,80));previous=normalizeIndices(reveal.getIndices());navigation='next_previous_round_trip'}
        else if(reveal.availableFragments?.().next){reveal.nextFragment();await new Promise(r=>setTimeout(r,50));next=normalizeIndices(reveal.getIndices());reveal.prevFragment();await new Promise(r=>setTimeout(r,50));previous=normalizeIndices(reveal.getIndices());navigation='single_slide_fragment_round_trip'}
        const config=reveal.getConfig();
        return {name,ready:reveal.isReady(),current:Boolean(reveal.getCurrentSlide()),slideCount:count,initial,next,previous,navigation,config:{width:config.width,height:config.height},orientation:{viewport:spec.viewport,canvas:{width:config.width,height:config.height},slides_checked:count,overflow_slides:overflow,overflow_details:overflowDetails.slice(0,40),vertical_occupancy:{minimum:Math.min(...occupancy),maximum:Math.max(...occupancy)}}};
      },{name,spec});
      if(pageErrors.length||failed.length)throw artifactFailure('deck emitted browser or asset errors during navigation','asset_load',{pageErrors,failed});
      if(measured.config.width!==spec.canvas.width||measured.config.height!==spec.canvas.height)throw artifactFailure(`${name} canvas mismatch`,'canvas',measured);
      const equal=(a,b)=>a.h===b.h&&a.v===b.v&&a.f===b.f;
      const indicesValid=[measured.initial,measured.next,measured.previous].every(value=>Number.isInteger(value.h)&&value.h>=0&&Number.isInteger(value.v)&&value.v>=0&&Number.isInteger(value.f)&&value.f>=-1);
      const navigationValid=indicesValid&&equal(measured.initial,measured.previous)&&(measured.navigation==='single_slide_stable'?measured.slideCount===1&&equal(measured.initial,measured.next):!equal(measured.initial,measured.next));
      if(!measured.ready||!measured.current||measured.slideCount<1||!navigationValid)throw artifactFailure(`${name} deck readiness or navigation failed`,'navigation',measured);
      shared ??= measured; specs[name].result=measured.orientation;
      await page.close();
    }
    const outcome={status:'passed',validator:'playwright_chromium',responsive_layout:'responsive-v2',reveal_ready:shared.ready,current_slide_exists:shared.current,slide_count:shared.slideCount,navigation:shared.navigation,initial_indices:shared.initial,next_indices:shared.next,previous_indices:shared.previous,relevant_asset_loads:relevantLoads.size,portrait:specs.portrait.result,landscape:specs.landscape.result};
    if(outcome.portrait.overflow_slides.length||outcome.landscape.overflow_slides.length)throw artifactFailure('one or more slides overflow the responsive canvas','layout_overflow',outcome);
    console.log(RESULT+JSON.stringify(outcome));
  } catch(error) {
    const artifactTimeout=error instanceof TimeoutError&&['asset_load','readiness','navigation'].includes(phase);
    console.error(FAILURE+JSON.stringify({phase:error.artifactFailure?error.phase:phase,reason:String(error.message||error),repairable:error.artifactFailure===true||artifactTimeout,context:error.context||{}}));
    process.exitCode=1;
  } finally { if(browser) await browser.close(); }
})();
";

#[derive(Debug, Error)]
pub(super) enum BrowserValidationError {
    #[error("Learning Deck browser validation deadline expired")]
    Deadline,
    #[error("Learning Deck browser validation I/O failed: {0}")]
    Io(#[source] TaskFileError),
    #[error("Learning Deck browser validation failed: {message}")]
    Validation {
        message: String,
        report: Map<String, Value>,
        repairable: bool,
    },
    #[error("Learning Deck browser validator reported an invalid outcome: {0}")]
    InvalidOutcome(String),
    #[error(transparent)]
    E2b(#[from] newsly_e2b::E2bError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

impl BrowserValidationError {
    pub(super) const fn repairable(&self) -> bool {
        matches!(
            self,
            Self::Validation {
                repairable: true,
                ..
            }
        )
    }

    pub(super) fn report(&self) -> Option<&Map<String, Value>> {
        match self {
            Self::Validation { report, .. } => Some(report),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{VALIDATION_SCRIPT, hosted_viewer_html};

    #[test]
    fn hosted_viewer_html_contains_the_runtime_responsive_contract() {
        let viewer = hosted_viewer_html(
            "<!doctype html><html><body><div class=\"reveal\"></div></body></html>",
        );

        let shell_marker = "data-newsly-learning-deck-controls=\"script\"";
        assert_eq!(viewer.matches(shell_marker).count(), 1);
        assert!(viewer.contains("portrait:{width:720,height:1280"));
        assert!(viewer.contains("landscape:{width:1280,height:720"));
        assert!(viewer.contains("newsly-learning-deck-portrait"));
        assert!(viewer.contains("scrollActivationWidth:null"));
        assert!(
            viewer.find(shell_marker).expect("shell marker")
                < viewer.rfind("</body>").expect("closing body")
        );
    }

    #[test]
    fn page_evaluate_helpers_are_defined_inside_the_serialized_callback() {
        let callback = VALIDATION_SCRIPT
            .split_once("page.evaluate(async ({name,spec})=>{")
            .expect("page.evaluate callback must exist")
            .1
            .split_once("},{name,spec});")
            .expect("page.evaluate callback must terminate")
            .0;

        let definition = callback
            .find("const normalizeIndices=")
            .expect("indices helper must be serialized with the callback");
        let first_use = callback
            .find("normalizeIndices(reveal.getIndices())")
            .expect("indices helper must be used by the callback");
        assert!(definition < first_use);
    }

    #[test]
    fn failure_reports_require_typed_nonempty_fields_and_known_repair_phases() {
        for raw in [
            serde_json::json!({"phase":"", "reason":"bad", "repairable":true}),
            serde_json::json!({"phase":"readiness", "reason":"bad"}),
            serde_json::json!({"phase":"readiness", "reason":4, "repairable":true}),
            serde_json::json!({"phase":"readiness", "reason":"bad", "repairable":"true"}),
            serde_json::json!({"phase":"browser_launch", "reason":"bad", "repairable":true}),
            serde_json::json!({"phase":"readiness", "reason":"bad", "repairable":true, "unknown":1}),
        ] {
            assert!(super::parse_failure_report(raw).is_err());
        }
        assert!(
            super::parse_failure_report(
                serde_json::json!({"phase":"readiness", "reason":"timed out", "repairable":true})
            )
            .is_ok()
        );
        assert!(super::parse_failure_report(serde_json::json!({"phase":"browser_launch", "reason":"unavailable", "repairable":false})).is_ok());
    }

    #[test]
    fn browser_launch_requires_a_minimum_remaining_budget() {
        let now = tokio::time::Instant::now();
        assert!(super::browser_deadline(now + std::time::Duration::from_secs(29), now).is_err());
        assert_eq!(
            super::browser_deadline(now + std::time::Duration::from_secs(60), now).unwrap(),
            now + std::time::Duration::from_secs(45)
        );
    }

    fn valid_outcome() -> super::BrowserValidationOutcome {
        let orientation = |width, height, canvas_width, canvas_height| {
            serde_json::json!({
                "viewport":{"width":width,"height":height},
                "canvas":{"width":canvas_width,"height":canvas_height},
                "slides_checked":2,"overflow_slides":[],
                "vertical_occupancy":{"minimum":0.2,"maximum":0.8}
            })
        };
        serde_json::from_value(serde_json::json!({
            "status":"passed","validator":"playwright_chromium","responsive_layout":"responsive-v2",
            "reveal_ready":true,"current_slide_exists":true,"slide_count":2,
            "navigation":"next_previous_round_trip",
            "initial_indices":{"h":0,"v":0,"f":-1},
            "next_indices":{"h":1,"v":0,"f":-1},
            "previous_indices":{"h":0,"v":0,"f":-1},"relevant_asset_loads":0,
            "portrait":orientation(390,844,720,1280),"landscape":orientation(844,390,1280,720)
        }))
        .unwrap()
    }

    #[test]
    fn passing_outcomes_require_actual_navigation_and_valid_measurements() {
        assert!(super::validate_outcome(&valid_outcome()).is_ok());
        let mut outcome = valid_outcome();
        outcome.next_indices = outcome.initial_indices.clone();
        assert!(super::validate_outcome(&outcome).is_err());
        let mut outcome = valid_outcome();
        outcome.previous_indices.h = 1;
        assert!(super::validate_outcome(&outcome).is_err());
        let mut outcome = valid_outcome();
        outcome.portrait.vertical_occupancy.minimum = f64::NAN;
        assert!(super::validate_outcome(&outcome).is_err());
        let mut outcome = valid_outcome();
        outcome.landscape.viewport.width = 1;
        assert!(super::validate_outcome(&outcome).is_err());
        let mut outcome = valid_outcome();
        outcome.navigation = "single_slide_stable".to_owned();
        assert!(super::validate_outcome(&outcome).is_err());
    }

    #[test]
    fn single_slide_fragment_and_stable_navigation_are_distinct() {
        let mut outcome = valid_outcome();
        outcome.slide_count = 1;
        outcome.portrait.slides_checked = 1;
        outcome.landscape.slides_checked = 1;
        outcome.navigation = "single_slide_stable".to_owned();
        outcome.next_indices = outcome.initial_indices.clone();
        assert!(super::validate_outcome(&outcome).is_ok());
        outcome.navigation = "single_slide_fragment_round_trip".to_owned();
        assert!(super::validate_outcome(&outcome).is_err());
        outcome.next_indices.f = 0;
        assert!(super::validate_outcome(&outcome).is_ok());
        outcome.next_indices.h = 1;
        assert!(super::validate_outcome(&outcome).is_err());
    }

    #[test]
    fn malformed_latest_report_cannot_reuse_an_earlier_pass() {
        assert!(
            super::prefixed_json("PREFIX={\"ok\":true}\nPREFIX={invalid}", "PREFIX=").is_none()
        );
    }
}
