use std::collections::BTreeMap;
use std::time::Duration;

use newsly_e2b::{
    CommandRequest, DirectE2bProvider, ExecutionTag, OutputLimits, SandboxHandle, SandboxProvider,
    SandboxUser,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::task_tools::TaskToolExecutor;

const VALIDATION_VIEWER_PATH: &str = "output/.newsly-viewer-validation.html";
const VALIDATION_SCRIPT_PATH: &str = "input/.newsly-browser-validation.cjs";
const RESULT_PREFIX: &str = "NEWSLY_BROWSER_VALIDATION=";
const FAILURE_PREFIX: &str = "NEWSLY_BROWSER_VALIDATION_FAILURE=";
const RESPONSIVE_LAYOUT: &str = "responsive-v2";

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CanvasSize {
    width: i64,
    height: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
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

pub(super) async fn validate_in_browser(
    provider: &DirectE2bProvider,
    sandbox: &SandboxHandle,
    tools: &TaskToolExecutor,
    workspace_path: &str,
    index_html: &str,
    deadline: Instant,
    cancellation: CancellationToken,
) -> Result<Map<String, Value>, BrowserValidationError> {
    let viewer = with_viewer_shell(index_html);
    tools
        .write_text(VALIDATION_VIEWER_PATH, viewer)
        .await
        .map_err(|error| BrowserValidationError::Io(error.to_string()))?;
    tools
        .write_text(VALIDATION_SCRIPT_PATH, VALIDATION_SCRIPT.to_owned())
        .await
        .map_err(|error| BrowserValidationError::Io(error.to_string()))?;

    let absolute_deadline = deadline.min(
        Instant::now()
            .checked_add(Duration::from_secs(45))
            .ok_or(BrowserValidationError::Deadline)?,
    );
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
            let reason = report
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("browser validation failed");
            let repairable = report
                .get("repairable")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            return Err(BrowserValidationError::Validation {
                message: reason.chars().take(4_000).collect(),
                report: report.as_object().cloned().unwrap_or_default(),
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

fn validate_outcome(outcome: &BrowserValidationOutcome) -> Result<(), BrowserValidationError> {
    let navigation = [
        "next_previous_round_trip",
        "single_slide_fragment_round_trip",
        "single_slide_stable",
    ];
    let complete = outcome.status == "passed"
        && outcome.validator == "playwright_chromium"
        && outcome.responsive_layout == RESPONSIVE_LAYOUT
        && outcome.reveal_ready
        && outcome.current_slide_exists
        && outcome.slide_count >= 1
        && navigation.contains(&outcome.navigation.as_str())
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
    output.lines().rev().find_map(|line| {
        line.strip_prefix(prefix)
            .and_then(|payload| serde_json::from_str(payload).ok())
    })
}

fn with_viewer_shell(index_html: &str) -> String {
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
const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const RESULT = 'NEWSLY_BROWSER_VALIDATION=';
const FAILURE = 'NEWSLY_BROWSER_VALIDATION_FAILURE=';
const specs = {
  portrait: { viewport: { width: 390, height: 844 }, canvas: { width: 720, height: 1280 } },
  landscape: { viewport: { width: 844, height: 390 }, canvas: { width: 1280, height: 720 } },
};
(async()=>{
  let browser;
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
      await page.goto(url,{waitUntil:'load'});
      await page.waitForFunction(()=>window.Reveal&&typeof Reveal.isReady==='function'&&Reveal.isReady());
      await page.waitForTimeout(750);
      if(pageErrors.length||failed.length) throw Object.assign(new Error('deck emitted browser or asset errors'),{phase:'asset_load',context:{pageErrors,failed}});
      const measured = await page.evaluate(async ({name,spec})=>{
        const reveal=window.Reveal; const slides=reveal.getSlides(); const overflow=[]; const occupancy=[];
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
          if(spills)overflow.push(current.id||`slide-${i+1}`);
        }
        reveal.slide(0,0,-1); await new Promise(r=>setTimeout(r,50));
        const initial=normalizeIndices(reveal.getIndices()); const count=slides.length; let next=initial; let previous=initial; let navigation='single_slide_stable';
        if(count>1){reveal.next();await new Promise(r=>setTimeout(r,80));next=normalizeIndices(reveal.getIndices());reveal.prev();await new Promise(r=>setTimeout(r,80));previous=normalizeIndices(reveal.getIndices());navigation='next_previous_round_trip'}
        else if(reveal.availableFragments?.().next){reveal.nextFragment();await new Promise(r=>setTimeout(r,50));next=normalizeIndices(reveal.getIndices());reveal.prevFragment();await new Promise(r=>setTimeout(r,50));previous=normalizeIndices(reveal.getIndices());navigation='single_slide_fragment_round_trip'}
        const config=reveal.getConfig();
        return {name,ready:reveal.isReady(),current:Boolean(reveal.getCurrentSlide()),slideCount:count,initial,next,previous,navigation,config:{width:config.width,height:config.height},orientation:{viewport:spec.viewport,canvas:{width:config.width,height:config.height},slides_checked:count,overflow_slides:overflow,vertical_occupancy:{minimum:Math.min(...occupancy),maximum:Math.max(...occupancy)}}};
      },{name,spec});
      if(measured.config.width!==spec.canvas.width||measured.config.height!==spec.canvas.height)throw Object.assign(new Error(`${name} canvas mismatch`),{phase:'canvas',context:measured});
      shared ??= measured; specs[name].result=measured.orientation;
      await page.close();
    }
    const outcome={status:'passed',validator:'playwright_chromium',responsive_layout:'responsive-v2',reveal_ready:shared.ready,current_slide_exists:shared.current,slide_count:shared.slideCount,navigation:shared.navigation,initial_indices:shared.initial,next_indices:shared.next,previous_indices:shared.previous,relevant_asset_loads:relevantLoads.size,portrait:specs.portrait.result,landscape:specs.landscape.result};
    if(outcome.portrait.overflow_slides.length||outcome.landscape.overflow_slides.length)throw Object.assign(new Error('one or more slides overflow the responsive canvas'),{phase:'layout_overflow',context:outcome});
    console.log(RESULT+JSON.stringify(outcome));
  } catch(error) {
    console.error(FAILURE+JSON.stringify({phase:error.phase||'validator',reason:String(error.message||error),repairable:true,context:error.context||{}}));
    process.exitCode=1;
  } finally { if(browser) await browser.close(); }
})();
";

#[derive(Debug, Error)]
pub(super) enum BrowserValidationError {
    #[error("Learning Deck browser validation deadline expired")]
    Deadline,
    #[error("Learning Deck browser validation I/O failed: {0}")]
    Io(String),
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
    use super::VALIDATION_SCRIPT;

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
}
