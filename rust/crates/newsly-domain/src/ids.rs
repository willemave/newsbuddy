use std::fmt::{self, Display, Formatter};

use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
#[error("database identifier must be greater than zero, got {0}")]
pub struct InvalidDatabaseId(i64);

macro_rules! database_id {
    ($name:ident) => {
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
        )]
        #[serde(transparent)]
        pub struct $name(i64);

        impl $name {
            /// Creates a typed identifier.
            ///
            /// # Errors
            ///
            /// Returns [`InvalidDatabaseId`] when `value` is not positive.
            pub fn new(value: i64) -> Result<Self, InvalidDatabaseId> {
                if value > 0 {
                    Ok(Self(value))
                } else {
                    Err(InvalidDatabaseId(value))
                }
            }

            pub const fn get(self) -> i64 {
                self.0
            }
        }

        impl TryFrom<i64> for $name {
            type Error = InvalidDatabaseId;

            fn try_from(value: i64) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl From<$name> for i64 {
            fn from(value: $name) -> Self {
                value.get()
            }
        }

        impl Display for $name {
            fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
                Display::fmt(&self.0, formatter)
            }
        }
    };
}

database_id!(UserId);
database_id!(ContentId);
database_id!(NewsItemId);
database_id!(ChatSessionId);
database_id!(LlmTaskId);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
#[error("generation must be nonnegative, got {0}")]
pub struct InvalidGeneration(i64);

macro_rules! generation {
    ($name:ident) => {
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
        )]
        #[serde(transparent)]
        pub struct $name(i64);

        impl $name {
            /// Creates a typed generation fence.
            ///
            /// # Errors
            ///
            /// Returns [`InvalidGeneration`] when `value` is negative.
            pub fn new(value: i64) -> Result<Self, InvalidGeneration> {
                if value >= 0 {
                    Ok(Self(value))
                } else {
                    Err(InvalidGeneration(value))
                }
            }

            pub const fn get(self) -> i64 {
                self.0
            }
        }

        impl TryFrom<i64> for $name {
            type Error = InvalidGeneration;

            fn try_from(value: i64) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl From<$name> for i64 {
            fn from(value: $name) -> Self {
                value.get()
            }
        }

        impl Display for $name {
            fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
                Display::fmt(&self.0, formatter)
            }
        }
    };
}

generation!(StreamGeneration);
generation!(BriefingVersion);

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
