//! One catalog owns chat tool identity, schemas and execution policy.
use super::canonical::{
    Inbox, Read, ReadState, Resolve, SavedState, Search, Sources, Subscribe, WebPage,
};
use super::{TaskToolExecutor, ToolDefinition, WebSearchInput, definition};
use newsly_domain::AggregatorKey;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::chat_turn) enum HostTool {
    SearchWeb,
    ReadWebPage,
    ResolveSource,
    ListSources,
    SubscribeToSource,
    SearchContent,
    ReadContent,
    SetReadState,
    SetSavedState,
    AddToInbox,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::chat_turn) enum SandboxTool {
    ExecuteBash,
    WriteFile,
    EditFile,
    ReadFile,
    ListFiles,
    CopyContentToWorkspace,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::chat_turn) enum ChatTool {
    Host(HostTool),
    Sandbox(SandboxTool),
}
impl HostTool {
    const ALL: [Self; 10] = [
        Self::SearchWeb,
        Self::ReadWebPage,
        Self::ResolveSource,
        Self::ListSources,
        Self::SubscribeToSource,
        Self::SearchContent,
        Self::ReadContent,
        Self::SetReadState,
        Self::SetSavedState,
        Self::AddToInbox,
    ];
    pub(super) const fn name(self) -> &'static str {
        match self {
            Self::SearchWeb => "search_web",
            Self::ReadWebPage => "read_web_page",
            Self::ResolveSource => "resolve_source",
            Self::ListSources => "list_sources",
            Self::SubscribeToSource => "subscribe_to_source",
            Self::SearchContent => "search_content",
            Self::ReadContent => "read_content",
            Self::SetReadState => "set_read_state",
            Self::SetSavedState => "set_saved_state",
            Self::AddToInbox => "add_to_inbox",
        }
    }
    const fn read_only(self) -> bool {
        !matches!(
            self,
            Self::SubscribeToSource | Self::SetReadState | Self::SetSavedState | Self::AddToInbox
        )
    }
    const fn cacheable(self) -> bool {
        matches!(
            self,
            Self::SearchWeb | Self::ReadWebPage | Self::ResolveSource
        )
    }
    fn definition(self) -> ToolDefinition {
        match self {
            Self::SearchWeb => definition::<WebSearchInput>(
                self.name(),
                "Search public web facts and source recommendations. Returns titles, URLs and snippets. Default limit 5.",
            ),
            Self::ReadWebPage => definition::<WebPage>(
                self.name(),
                "Read a public HTTP(S) page as inert untrusted text, links and feed hints. No JavaScript or sandbox. Default max_chars 12000.",
            ),
            Self::ResolveSource => definition::<Resolve>(
                self.name(),
                &format!(
                    "Built-in aggregators: {}. Resolve one named show/publication/aggregator or a direct Apple, RSS or homepage URL into validated source options. This is identity resolution, not generic recommendations; use search_web for recommendations. Return ambiguous or unavailable identities truthfully. Hosts disambiguate podcast hosts. Options include activity and subscription cards.",
                    AggregatorKey::ALL
                        .into_iter()
                        .map(|key| format!("{} ({})", key.display_name(), key.as_str()))
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            ),
            Self::ListSources => definition::<Sources>(
                self.name(),
                "List the user's followed sources with stable source references and subscription state. Optional query/kind (podcast/publication/aggregator), active_only defaults true, limit 20 and offset 0.",
            ),
            Self::SubscribeToSource => definition::<Subscribe>(
                self.name(),
                "Follow one exact source reference explicitly requested by the user. The host validates the feed URL and source ownership. Supply topics only when the user requests an aggregator topic filter; omit to preserve the filter, [] clears it.",
            ),
            Self::SearchContent => definition::<Search>(
                self.name(),
                "Browse or lexically search scope knowledge (saved), content (inbox and saved), or news. Query may be omitted. Source must be a typed source reference from list_sources or resolve_source. State and source filters apply before paging. Defaults limit 20, offset 0. Empty results remain empty.",
            ),
            Self::ReadContent => definition::<Read>(
                self.name(),
                "Read one typed content or news reference after current host authorization. Content bodies support offset/max_bytes. News returns its summary and links; use read_web_page to inspect linked articles.",
            ),
            Self::SetReadState => definition::<ReadState>(
                self.name(),
                "Set read/unread state for up to 20 typed content/news references. All references must be authorized; outcomes report committed state.",
            ),
            Self::SetSavedState => definition::<SavedState>(
                self.name(),
                "Set saved Knowledge state for up to 20 typed content references. For News first add_to_inbox and save the returned content reference.",
            ),
            Self::AddToInbox => definition::<Inbox>(
                self.name(),
                "Add one public URL string or authorized content/news reference to the user's inbox. Returns the canonical content reference and whether it already existed.",
            ),
        }
    }
}
impl SandboxTool {
    const ALL: [Self; 6] = [
        Self::ExecuteBash,
        Self::WriteFile,
        Self::EditFile,
        Self::ReadFile,
        Self::ListFiles,
        Self::CopyContentToWorkspace,
    ];
    pub(super) const fn name(self) -> &'static str {
        match self {
            Self::ExecuteBash => "execute_bash",
            Self::WriteFile => "write_file",
            Self::EditFile => "edit_file",
            Self::ReadFile => "read_file",
            Self::ListFiles => "list_files",
            Self::CopyContentToWorkspace => "copy_content_to_workspace",
        }
    }
    pub(super) const fn transport_name(self) -> &'static str {
        match self {
            Self::CopyContentToWorkspace => "write_knowledge_items",
            _ => self.name(),
        }
    }
}
impl ChatTool {
    pub(in crate::chat_turn) fn all() -> impl Iterator<Item = Self> {
        HostTool::ALL
            .into_iter()
            .map(Self::Host)
            .chain(SandboxTool::ALL.into_iter().map(Self::Sandbox))
    }
    pub(in crate::chat_turn) fn name(self) -> &'static str {
        match self {
            Self::Host(tool) => tool.name(),
            Self::Sandbox(tool) => tool.name(),
        }
    }
    pub(super) fn parse(name: &str) -> Option<Self> {
        Self::all().find(|tool| tool.name() == name)
    }
    pub(super) fn read_only(self) -> bool {
        matches!(self, Self::Host(tool) if tool.read_only())
    }
    pub(super) fn cacheable(self) -> bool {
        matches!(self, Self::Host(tool) if tool.cacheable())
    }
    pub(super) fn definition(self) -> ToolDefinition {
        match self {
            Self::Host(tool) => tool.definition(),
            Self::Sandbox(tool) => {
                let mut definition = TaskToolExecutor::sandbox_definitions()
                    .into_iter()
                    .find(|definition| definition.name == tool.transport_name())
                    .expect("sandbox catalog has every declared tool");
                tool.name().clone_into(&mut definition.name);
                definition
            }
        }
    }
}
