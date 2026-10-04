use std::fmt::{self, Display, Formatter};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Opaque ownership token for a single durable queue lease.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct LeaseToken(Uuid);

impl LeaseToken {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    pub const fn get(self) -> Uuid {
        self.0
    }
}

impl Default for LeaseToken {
    fn default() -> Self {
        Self::new()
    }
}

impl From<Uuid> for LeaseToken {
    fn from(value: Uuid) -> Self {
        Self(value)
    }
}

impl From<LeaseToken> for Uuid {
    fn from(value: LeaseToken) -> Self {
        value.get()
    }
}

impl Display for LeaseToken {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        Display::fmt(&self.0, formatter)
    }
}
