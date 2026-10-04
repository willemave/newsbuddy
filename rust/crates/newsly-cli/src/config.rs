//! Persistent and effective CLI configuration.

use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

pub(crate) const ENV_CONFIG_PATH: &str = "NEWSBUDDY_CONFIG";
pub(crate) const ENV_SERVER_URL: &str = "NEWSBUDDY_SERVER";
pub(crate) const ENV_API_KEY: &str = "NEWSBUDDY_API_KEY";

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct FileConfig {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub(crate) server_url: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub(crate) api_key: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub(crate) library_root: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RuntimeConfig {
    pub(crate) path: PathBuf,
    pub(crate) server_url: String,
    pub(crate) api_key: String,
    pub(crate) library_root: PathBuf,
}

impl RuntimeConfig {
    /// Validate configuration for unauthenticated server operations such as CLI linking.
    ///
    /// # Errors
    ///
    /// Returns [`ConfigError::MissingServerUrl`] when no server URL is configured.
    pub(crate) fn validate_server_only(&self) -> Result<(), ConfigError> {
        if self.server_url.trim().is_empty() {
            return Err(ConfigError::MissingServerUrl);
        }
        Ok(())
    }

    /// Validate configuration for authenticated API operations.
    ///
    /// # Errors
    ///
    /// Returns the applicable missing-server or missing-API-key error.
    pub(crate) fn validate_remote(&self) -> Result<(), ConfigError> {
        self.validate_server_only()?;
        if self.api_key.trim().is_empty() {
            return Err(ConfigError::MissingApiKey);
        }
        Ok(())
    }
}

#[derive(Debug, Error)]
pub(crate) enum ConfigError {
    #[error("missing server_url; run `newsbuddy config set server ...` first")]
    MissingServerUrl,
    #[error("missing api_key; run `newsbuddy config set api-key ...` first")]
    MissingApiKey,
    #[error("failed to access CLI configuration: {0}")]
    Io(#[from] std::io::Error),
    #[error("failed to decode CLI configuration: {0}")]
    Json(#[from] serde_json::Error),
}

/// Resolve a config path using an explicit or canonical environment override.
pub(crate) fn resolve_path(explicit: &str) -> PathBuf {
    resolve_path_with(
        explicit,
        &environment_value,
        home_directory().as_deref(),
        &current_directory(),
    )
}

/// Load a config file. A missing or whitespace-only file is an empty configuration.
///
/// # Errors
///
/// Returns an error when the file cannot be read or contains invalid JSON.
pub(crate) fn load(path: impl AsRef<Path>) -> Result<FileConfig, ConfigError> {
    let path = path.as_ref();
    let payload = match fs::read_to_string(path) {
        Ok(payload) => payload,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(FileConfig::default());
        }
        Err(error) => return Err(error.into()),
    };
    if payload.trim().is_empty() {
        return Ok(FileConfig::default());
    }

    let mut config: FileConfig = serde_json::from_str(&payload)?;
    normalize_file_config(&mut config);
    Ok(config)
}

/// Save a config file as pretty JSON with owner-only permissions.
///
/// # Errors
///
/// Returns an error when the config cannot be encoded or written.
pub(crate) fn save(path: impl AsRef<Path>, config: &FileConfig) -> Result<(), ConfigError> {
    let path = path.as_ref();
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }

    let mut payload = serde_json::to_vec_pretty(config)?;
    payload.push(b'\n');
    write_owner_only(path, &payload)?;
    Ok(())
}

/// Update and persist the current file configuration.
///
/// # Errors
///
/// Returns an error when the existing config cannot be loaded or the update cannot be saved.
pub(crate) fn update(
    path: impl AsRef<Path>,
    update: impl FnOnce(FileConfig) -> FileConfig,
) -> Result<FileConfig, ConfigError> {
    let path = path.as_ref();
    let mut config = update(load(path)?);
    normalize_file_config(&mut config);
    save(path, &config)?;
    Ok(config)
}

/// Resolve the effective runtime configuration.
///
/// Precedence is command-line override, environment, config file, then defaults.
///
/// # Errors
///
/// Returns an error when the selected config file cannot be loaded.
pub(crate) fn resolve_runtime(
    path_override: &str,
    server_override: &str,
    api_key_override: &str,
) -> Result<RuntimeConfig, ConfigError> {
    resolve_runtime_with(
        path_override,
        server_override,
        api_key_override,
        &environment_value,
        home_directory().as_deref(),
        &current_directory(),
    )
}

/// Mask a configured API key while retaining enough context to identify it.
pub(crate) fn masked_api_key(raw: &str) -> String {
    let raw = raw.trim();
    if raw.is_empty() {
        return String::new();
    }
    let characters: Vec<char> = raw.chars().collect();
    if characters.len() <= 8 {
        return "********".to_owned();
    }

    let prefix: String = characters[..4].iter().collect();
    let suffix: String = characters[characters.len() - 4..].iter().collect();
    format!(
        "{prefix}{}{suffix}",
        "*".repeat(characters.len().saturating_sub(8))
    )
}

/// Expand a leading `~/` and make a path absolute without requiring it to exist.
pub(crate) fn clean_path(path: &str) -> PathBuf {
    clean_path_with(path, home_directory().as_deref(), &current_directory())
}

fn normalize_file_config(config: &mut FileConfig) {
    config.server_url = config.server_url.trim().to_owned();
    config.api_key = config.api_key.trim().to_owned();
    let library_root = config.library_root.trim();
    config.library_root = if library_root.is_empty() {
        String::new()
    } else {
        clean_path(library_root).to_string_lossy().into_owned()
    };
}

fn resolve_runtime_with(
    path_override: &str,
    server_override: &str,
    api_key_override: &str,
    env_value: &impl Fn(&str) -> Option<String>,
    home: Option<&Path>,
    current_dir: &Path,
) -> Result<RuntimeConfig, ConfigError> {
    let path = resolve_path_with(path_override, env_value, home, current_dir);
    let file_config = load(&path)?;
    let server_url = env_value(ENV_SERVER_URL)
        .and_then(|value| nonempty_trimmed(&value))
        .unwrap_or(file_config.server_url);
    let api_key = env_value(ENV_API_KEY)
        .and_then(|value| nonempty_trimmed(&value))
        .unwrap_or(file_config.api_key);

    let server_url = nonempty_trimmed(server_override).unwrap_or(server_url);
    let api_key = nonempty_trimmed(api_key_override).unwrap_or(api_key);
    let library_root = if file_config.library_root.is_empty() {
        default_library_root_with_home(home)
    } else {
        clean_path_with(&file_config.library_root, home, current_dir)
    };

    Ok(RuntimeConfig {
        path,
        server_url,
        api_key,
        library_root,
    })
}

fn resolve_path_with(
    explicit: &str,
    env_value: &impl Fn(&str) -> Option<String>,
    home: Option<&Path>,
    current_dir: &Path,
) -> PathBuf {
    if let Some(explicit) = nonempty_trimmed(explicit) {
        return clean_path_with(&explicit, home, current_dir);
    }
    if let Some(config_path) = env_value(ENV_CONFIG_PATH).and_then(|value| nonempty_trimmed(&value))
    {
        return clean_path_with(&config_path, home, current_dir);
    }
    default_path_with_home(home)
}

fn nonempty_trimmed(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn default_path_with_home(home: Option<&Path>) -> PathBuf {
    home.map_or_else(
        || PathBuf::from(".newsbuddy.json"),
        |home| home.join(".config/newsbuddy/config.json"),
    )
}

fn default_library_root_with_home(home: Option<&Path>) -> PathBuf {
    home.map_or_else(
        || PathBuf::from(".newsbuddy-library"),
        |home| home.join(".local/share/newsbuddy/library"),
    )
}

fn clean_path_with(path: &str, home: Option<&Path>, current_dir: &Path) -> PathBuf {
    if path.is_empty() {
        return PathBuf::new();
    }

    let expanded = path.strip_prefix("~/").map_or_else(
        || PathBuf::from(path),
        |suffix| home.map_or_else(|| PathBuf::from(path), |home| home.join(suffix)),
    );
    let absolute = if expanded.is_absolute() {
        expanded
    } else {
        current_dir.join(expanded)
    };
    normalize_path(&absolute)
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if normalized.file_name().is_some() {
                    normalized.pop();
                }
            }
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => normalized.push(component.as_os_str()),
            Component::Normal(part) => normalized.push(part),
        }
    }
    normalized
}

fn home_directory() -> Option<PathBuf> {
    env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .or_else(|| env::var_os("USERPROFILE").filter(|value| !value.is_empty()))
        .map(PathBuf::from)
}

fn environment_value(name: &str) -> Option<String> {
    env::var(name).ok()
}

fn current_directory() -> PathBuf {
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn write_owner_only(path: &Path, payload: &[u8]) -> std::io::Result<()> {
    use std::io::Write;

    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(payload)?;
    temporary.as_file().sync_all()?;
    set_owner_only_permissions(temporary.path())?;
    temporary.persist(path).map_err(|error| error.error)?;
    set_owner_only_permissions(path)
}

#[cfg(unix)]
fn set_owner_only_permissions(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_owner_only_permissions(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    fn env_from(values: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let values: Vec<(String, String)> = values
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        move |name| {
            values
                .iter()
                .find(|(candidate, _)| candidate == name)
                .map(|(_, value)| value.clone())
        }
    }

    #[test]
    fn runtime_precedence_is_flags_environment_file_defaults() {
        let directory = tempdir().expect("temporary directory");
        let config_path = directory.path().join("config.json");
        save(
            &config_path,
            &FileConfig {
                server_url: "https://file.example.com".to_owned(),
                api_key: "file-key".to_owned(),
                library_root: String::new(),
            },
        )
        .expect("save config");
        let config_path_text = config_path.to_string_lossy().into_owned();
        let env = env_from(&[
            (ENV_SERVER_URL, "https://env.example.com"),
            (ENV_API_KEY, "env-key"),
        ]);

        let runtime = resolve_runtime_with(
            &config_path_text,
            "https://flag.example.com",
            "flag-key",
            &env,
            Some(directory.path()),
            directory.path(),
        )
        .expect("resolve runtime");

        assert_eq!(runtime.server_url, "https://flag.example.com");
        assert_eq!(runtime.api_key, "flag-key");
        assert_eq!(
            runtime.library_root,
            directory.path().join(".local/share/newsbuddy/library")
        );
    }

    #[test]
    fn path_resolution_supports_the_canonical_environment_name() {
        let directory = tempdir().expect("temporary directory");
        let target = directory.path().join("config.json");
        let target_text = target.to_string_lossy().into_owned();
        let bindings = [(ENV_CONFIG_PATH, target_text.as_str())];
        let env = env_from(&bindings);
        assert_eq!(
            resolve_path_with("", &env, Some(directory.path()), directory.path()),
            target
        );
    }

    #[test]
    fn explicit_path_wins_and_tilde_is_expanded() {
        let directory = tempdir().expect("temporary directory");
        let env = env_from(&[(ENV_CONFIG_PATH, "/ignored/config.json")]);
        let resolved = resolve_path_with(
            "~/preferred/config.json",
            &env,
            Some(directory.path()),
            Path::new("/work"),
        );
        assert_eq!(resolved, directory.path().join("preferred/config.json"));
    }

    #[test]
    fn save_load_and_update_normalize_values() {
        let directory = tempdir().expect("temporary directory");
        let path = directory.path().join("nested/config.json");
        save(
            &path,
            &FileConfig {
                server_url: " https://example.com ".to_owned(),
                api_key: " key ".to_owned(),
                library_root: String::new(),
            },
        )
        .expect("save config");
        let loaded = load(&path).expect("load config");
        assert_eq!(loaded.server_url, "https://example.com");
        assert_eq!(loaded.api_key, "key");

        let updated = update(&path, |mut config| {
            config.library_root = " ./library/../knowledge ".to_owned();
            config
        })
        .expect("update config");
        assert!(updated.library_root.ends_with("/knowledge"));
    }

    #[cfg(unix)]
    #[test]
    fn save_enforces_owner_only_mode() {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempdir().expect("temporary directory");
        let path = directory.path().join("config.json");
        save(&path, &FileConfig::default()).expect("save config");
        let mode = fs::metadata(path)
            .expect("config metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
    }

    #[test]
    fn missing_and_blank_files_are_empty_configs() {
        let directory = tempdir().expect("temporary directory");
        let missing = directory.path().join("missing.json");
        assert_eq!(load(missing).expect("load missing"), FileConfig::default());

        let blank = directory.path().join("blank.json");
        fs::write(&blank, " \n\t").expect("write blank config");
        assert_eq!(load(blank).expect("load blank"), FileConfig::default());
    }

    #[test]
    fn api_key_mask_preserves_only_outer_four_characters() {
        assert_eq!(masked_api_key(""), "");
        assert_eq!(masked_api_key("short"), "********");
        assert_eq!(masked_api_key("12345678"), "********");
        assert_eq!(
            masked_api_key("newsly_ak_1234567890"),
            "news************7890"
        );
    }

    #[test]
    fn remote_validation_preserves_actionable_messages() {
        let mut runtime = RuntimeConfig {
            path: PathBuf::from("config.json"),
            server_url: String::new(),
            api_key: String::new(),
            library_root: PathBuf::from("library"),
        };
        assert_eq!(
            runtime
                .validate_remote()
                .expect_err("missing server")
                .to_string(),
            "missing server_url; run `newsbuddy config set server ...` first"
        );
        runtime.server_url = "https://example.com".to_owned();
        assert_eq!(
            runtime
                .validate_remote()
                .expect_err("missing key")
                .to_string(),
            "missing api_key; run `newsbuddy config set api-key ...` first"
        );
    }
}
