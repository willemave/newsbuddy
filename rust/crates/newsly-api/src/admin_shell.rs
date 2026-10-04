use crate::admin_api_keys::escape_html;

pub(super) fn admin_shell(title: &str) -> String {
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"robots\" content=\"noindex,nofollow,noarchive\"><title>{}</title><style>:root{{color-scheme:light}}body{{font:15px system-ui;margin:0;background:#f7f7f5;color:#191919}}main{{max-width:1150px;margin:3rem auto;padding:0 1rem}}nav{{margin-bottom:2rem}}table{{border-collapse:collapse;width:100%;background:white;margin-bottom:2rem}}th,td{{text-align:left;padding:.65rem;border:1px solid #ddd;vertical-align:top}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;margin:1.5rem 0}}.cards div,article,pre,section{{background:white;border:1px solid #ddd;padding:1rem}}.cards strong,.cards span{{display:block}}.cards strong{{font-size:1.6rem}}article{{margin:1rem 0}}pre,blockquote{{white-space:pre-wrap;overflow-wrap:anywhere}}label{{display:block;margin:.5rem 0}}input,select,textarea,button{{font:inherit;padding:.55rem;margin:.25rem;box-sizing:border-box}}button{{cursor:pointer}}.hidden{{display:none}}.login{{max-width:400px}}#error-message{{color:#a00}}</style></head><body>",
        escape_html(title)
    )
}
