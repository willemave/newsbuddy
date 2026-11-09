use std::collections::BTreeSet;
use std::path::Path;

use super::DECK_KIT_FILES;

fn files_below(root: &Path, dir: &Path, out: &mut BTreeSet<String>) {
    for entry in std::fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            files_below(root, &path, out);
        } else {
            let relative = path
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            out.insert(format!("rust/assets/deckkit/{relative}"));
        }
    }
}

#[test]
fn deck_kit_file_list_covers_every_installed_file() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../assets/deckkit");
    let mut on_disk = BTreeSet::new();
    files_below(&root, &root, &mut on_disk);
    let listed: BTreeSet<String> = DECK_KIT_FILES
        .iter()
        .map(|(path, _)| (*path).to_owned())
        .collect();

    assert_eq!(listed, on_disk);
}

#[test]
fn deck_kit_base_assets_leave_room_for_figures_under_the_asset_cap() {
    // `deckkit new` ships kit.css, kit-init.js, and six fonts; math adds KaTeX CSS and 20 fonts.
    let script = std::str::from_utf8(
        DECK_KIT_FILES
            .iter()
            .find(|(path, _)| path.ends_with("bin/deckkit"))
            .unwrap()
            .1,
    )
    .unwrap();
    let kit_css = std::str::from_utf8(
        DECK_KIT_FILES
            .iter()
            .find(|(path, _)| path.ends_with("kit.css"))
            .unwrap()
            .1,
    )
    .unwrap();

    assert_eq!(kit_css.matches("url(\"fonts/").count(), 6);
    assert!(script.contains(r#"cp "$KIT/kit.css" "$KIT/kit-init.js" output/assets/"#));
    assert!(!script.split("math)").next().unwrap().contains("katex"));
}
