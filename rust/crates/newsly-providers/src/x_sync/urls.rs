use reqwest::Url;
use scraper::{Html, Selector};

pub(super) fn normalize_external_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") {
        return None;
    }
    let host = url
        .host_str()?
        .trim_end_matches('.')
        .trim_start_matches("www.")
        .to_ascii_lowercase();
    if ["x.com", "twitter.com", "t.co"]
        .iter()
        .any(|domain| host == *domain || host.ends_with(&format!(".{domain}")))
    {
        return None;
    }
    if url.scheme() != "https" && url.set_scheme("https").is_err() {
        return None;
    }
    Some(url.to_string())
}

pub(super) fn trusted_short_url(value: &str) -> Option<Url> {
    let url = Url::parse(value.trim()).ok()?;
    let host = url.host_str()?.trim_end_matches('.').to_ascii_lowercase();
    (matches!(url.scheme(), "http" | "https") && host == "t.co").then_some(url)
}

pub(super) fn html_redirect_url(body: &str) -> Option<String> {
    let document = Html::parse_document(body);
    let meta_selector = Selector::parse("meta[http-equiv][content]").ok()?;
    for element in document.select(&meta_selector) {
        let value = element.value();
        if value
            .attr("http-equiv")
            .is_some_and(|attribute| attribute.eq_ignore_ascii_case("refresh"))
            && let Some(target) = refresh_content_url(value.attr("content")?)
        {
            return Some(target);
        }
    }
    let script_selector = Selector::parse("script").ok()?;
    document
        .select(&script_selector)
        .find_map(|element| script_location_replace(&element.text().collect::<String>()))
}

fn refresh_content_url(content: &str) -> Option<String> {
    let lowered = content.to_ascii_lowercase();
    let index = lowered.find("url=")? + 4;
    let target = content.get(index..)?.trim().trim_matches(['\'', '"']);
    (!target.is_empty()).then(|| target.to_owned())
}

fn script_location_replace(script: &str) -> Option<String> {
    let lowered = script.to_ascii_lowercase();
    let index = lowered.find("location.replace")? + "location.replace".len();
    let argument = script.get(index..)?.trim_start();
    let argument = argument.strip_prefix('(')?.trim_start();
    let quote = argument.chars().next()?;
    if !matches!(quote, '\'' | '"') {
        return None;
    }
    let remainder = argument.get(quote.len_utf8()..)?;
    let end = remainder.find(quote)?;
    let target = remainder.get(..end)?.replace("\\/", "/");
    (!target.is_empty()).then_some(target)
}
