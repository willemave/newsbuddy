-- Newsly SQLx baseline generated from a fresh PostgreSQL database migrated by Alembic.
-- Frozen Alembic head: 20260829_02
-- SQLx version: 20260830000000
-- Generated schema is PostgreSQL 15 compatible; do not edit after adoption.

--
-- PostgreSQL database dump
--




--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';




--
-- Name: agent_data_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_data_files (
    id integer NOT NULL,
    user_id integer NOT NULL,
    document_kind character varying(32) NOT NULL,
    document_key character varying(255) NOT NULL,
    path character varying(1024) NOT NULL,
    stale_paths jsonb DEFAULT '[]'::jsonb NOT NULL,
    checksum_sha256 character varying(64) NOT NULL,
    index_record jsonb NOT NULL,
    byte_size integer DEFAULT 0 NOT NULL,
    revision bigint NOT NULL,
    deleted_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_data_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_data_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_data_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_data_files_id_seq OWNED BY public.agent_data_files.id;


--
-- Name: agent_vm_system_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_vm_system_state (
    id integer NOT NULL,
    sandbox_id character varying(255),
    template_revision character varying(255),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_vm_system_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_vm_system_state_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_vm_system_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_vm_system_state_id_seq OWNED BY public.agent_vm_system_state.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: analytics_interactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analytics_interactions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer NOT NULL,
    interaction_type character varying(32) NOT NULL,
    interaction_id character varying(36) NOT NULL,
    surface character varying(64),
    context_data json DEFAULT '{}'::json NOT NULL,
    occurred_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: analytics_interactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analytics_interactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analytics_interactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analytics_interactions_id_seq OWNED BY public.analytics_interactions.id;


--
-- Name: audio_episodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audio_episodes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    kind character varying(50) NOT NULL,
    status character varying(20) NOT NULL,
    title character varying(255) NOT NULL,
    source_content_id integer,
    input_hash character varying(64) NOT NULL,
    source_item_ids jsonb NOT NULL,
    source_snapshot jsonb NOT NULL,
    script jsonb,
    script_text text,
    prompt_version integer NOT NULL,
    model character varying(100),
    audio_storage_path character varying(2048),
    audio_content_type character varying(100) NOT NULL,
    duration_seconds integer,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    share_enabled boolean NOT NULL,
    share_token_hash character varying(64),
    share_token_nonce character varying(64),
    episode_group_id character varying(64),
    chapter_index integer,
    CONSTRAINT ck_audio_episodes_chapter_index_nonnegative CHECK (((chapter_index IS NULL) OR (chapter_index >= 0))),
    CONSTRAINT ck_audio_episodes_chapter_metadata_pair CHECK (((episode_group_id IS NULL) = (chapter_index IS NULL)))
);


--
-- Name: audio_episodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audio_episodes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audio_episodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audio_episodes_id_seq OWNED BY public.audio_episodes.id;


--
-- Name: briefing_lenses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefing_lenses (
    id integer NOT NULL,
    user_id integer NOT NULL,
    key character varying(64) NOT NULL,
    tier character varying(16) NOT NULL,
    title character varying(220) NOT NULL,
    deck text NOT NULL,
    "position" integer NOT NULL,
    status character varying(16) NOT NULL,
    centroid jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    retired_at timestamp without time zone,
    centroid_weight integer NOT NULL,
    centroid_model character varying(120),
    routing_rule text
);


--
-- Name: briefing_lenses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.briefing_lenses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: briefing_lenses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.briefing_lenses_id_seq OWNED BY public.briefing_lenses.id;


--
-- Name: briefing_pending_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefing_pending_sources (
    id integer NOT NULL,
    user_id integer NOT NULL,
    lens_key character varying(64),
    source_kind character varying(16) NOT NULL,
    source_id integer NOT NULL,
    enqueued_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: briefing_pending_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.briefing_pending_sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: briefing_pending_sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.briefing_pending_sources_id_seq OWNED BY public.briefing_pending_sources.id;


--
-- Name: briefing_segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefing_segments (
    id integer NOT NULL,
    lens_id integer NOT NULL,
    user_id integer NOT NULL,
    blocks jsonb NOT NULL,
    markdown_raw text NOT NULL,
    narration_text text NOT NULL,
    source_keys jsonb NOT NULL,
    status character varying(16) NOT NULL,
    model character varying(64) NOT NULL,
    prompt_version character varying(16) NOT NULL,
    input_tokens integer,
    output_tokens integer,
    generation_ms integer,
    warnings jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    event_groups jsonb
);


--
-- Name: briefing_segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.briefing_segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: briefing_segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.briefing_segments_id_seq OWNED BY public.briefing_segments.id;


--
-- Name: briefing_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefing_states (
    user_id integer NOT NULL,
    version integer NOT NULL,
    masthead_title character varying(220) NOT NULL,
    masthead_deck text NOT NULL,
    last_append_at timestamp without time zone,
    last_sweep_at timestamp without time zone
);


--
-- Name: briefing_states_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.briefing_states_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: briefing_states_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.briefing_states_user_id_seq OWNED BY public.briefing_states.user_id;


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id integer NOT NULL,
    session_id integer NOT NULL,
    message_list text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    status character varying(20) DEFAULT 'completed'::character varying NOT NULL,
    error text,
    render_metadata json,
    processing_context json,
    partial_text text,
    stream_generation integer,
    stream_revision integer,
    stream_updated_at timestamp with time zone,
    deep_research_response_id character varying(255),
    tool_progress jsonb,
    tool_progress_revision integer,
    tool_progress_updated_at timestamp with time zone
);


--
-- Name: chat_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chat_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chat_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chat_messages_id_seq OWNED BY public.chat_messages.id;


--
-- Name: chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_sessions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer,
    title character varying(500),
    session_type character varying(50),
    topic character varying(500),
    llm_model character varying(100) DEFAULT 'openai:gpt-5.4'::character varying NOT NULL,
    llm_provider character varying(50) DEFAULT 'openai'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone,
    last_message_at timestamp without time zone,
    is_archived boolean DEFAULT false NOT NULL,
    context_snapshot text,
    parent_session_id integer,
    council_persona_id character varying(64),
    council_persona_name character varying(120),
    council_persona_prompt text,
    council_mode boolean NOT NULL,
    active_child_session_id integer,
    branch_start_message_id integer,
    council_message_id integer,
    is_hidden_from_history boolean NOT NULL,
    news_item_id integer
);


--
-- Name: chat_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chat_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chat_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chat_sessions_id_seq OWNED BY public.chat_sessions.id;


--
-- Name: cli_link_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cli_link_sessions (
    id integer NOT NULL,
    session_id character varying(64) NOT NULL,
    approve_token_hash character varying(128) NOT NULL,
    poll_token_hash character varying(128) NOT NULL,
    requested_device_name character varying(255),
    status character varying(32) NOT NULL,
    approved_by_user_id integer,
    user_api_key_id integer,
    issued_api_key_plaintext text,
    expires_at timestamp without time zone NOT NULL,
    approved_at timestamp without time zone,
    claimed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: cli_link_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cli_link_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cli_link_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cli_link_sessions_id_seq OWNED BY public.cli_link_sessions.id;


--
-- Name: consumed_refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.consumed_refresh_tokens (
    token_hash character varying(64) NOT NULL,
    user_id integer NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone DEFAULT now() NOT NULL,
    attempt_id character varying(36),
    replay_payload_encrypted text,
    replay_expires_at timestamp with time zone
);


--
-- Name: content_bodies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_bodies (
    content_id integer NOT NULL,
    variant character varying(20) NOT NULL,
    storage_provider character varying(32) NOT NULL,
    storage_bucket character varying(255),
    storage_key character varying(2048) NOT NULL,
    content_format character varying(32) NOT NULL,
    sha256 character varying(64) NOT NULL,
    byte_size integer DEFAULT 0 NOT NULL,
    char_count integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: content_discussions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_discussions (
    id integer NOT NULL,
    content_id integer NOT NULL,
    platform character varying(50),
    status character varying(20) NOT NULL,
    discussion_data json DEFAULT '{}'::json NOT NULL,
    error_message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    fetched_at timestamp without time zone
);


--
-- Name: content_discussions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.content_discussions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: content_discussions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.content_discussions_id_seq OWNED BY public.content_discussions.id;


--
-- Name: content_knowledge_saves; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_knowledge_saves (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer NOT NULL,
    saved_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: content_favorites_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.content_favorites_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: content_favorites_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.content_favorites_id_seq OWNED BY public.content_knowledge_saves.id;


--
-- Name: content_read_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_read_status (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer NOT NULL,
    read_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: content_read_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.content_read_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: content_read_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.content_read_status_id_seq OWNED BY public.content_read_status.id;


--
-- Name: content_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_status (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer NOT NULL,
    status character varying(20) DEFAULT 'inbox'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: content_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.content_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: content_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.content_status_id_seq OWNED BY public.content_status.id;


--
-- Name: content_unlikes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_unlikes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    content_id integer NOT NULL,
    unliked_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: content_unlikes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.content_unlikes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: content_unlikes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.content_unlikes_id_seq OWNED BY public.content_unlikes.id;


--
-- Name: contents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contents (
    id integer NOT NULL,
    content_type character varying(20) NOT NULL,
    url character varying(2048) NOT NULL,
    title character varying(500),
    source character varying(100),
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    error_message text,
    retry_count integer DEFAULT 0,
    classification character varying(20),
    checked_out_by character varying(100),
    checked_out_at timestamp without time zone,
    content_metadata json DEFAULT '{}'::json NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone,
    processed_at timestamp without time zone,
    publication_date timestamp without time zone,
    platform character varying(50),
    is_aggregate boolean NOT NULL,
    source_url character varying(2048),
    search_text text
);


--
-- Name: contents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contents_id_seq OWNED BY public.contents.id;


--
-- Name: daily_news_digests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.daily_news_digests (
    id integer NOT NULL,
    user_id integer NOT NULL,
    local_date date NOT NULL,
    timezone character varying(100) NOT NULL,
    title character varying(240) NOT NULL,
    summary text NOT NULL,
    key_points json NOT NULL,
    source_content_ids json NOT NULL,
    source_count integer NOT NULL,
    llm_model character varying(120) NOT NULL,
    generated_at timestamp without time zone NOT NULL,
    read_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    coverage_end_at timestamp without time zone,
    bullet_details json DEFAULT '[]'::json NOT NULL
);


--
-- Name: daily_news_digests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.daily_news_digests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: daily_news_digests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.daily_news_digests_id_seq OWNED BY public.daily_news_digests.id;


--
-- Name: event_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_logs (
    id integer NOT NULL,
    event_type character varying(50) NOT NULL,
    event_name character varying(100),
    status character varying(20),
    data json DEFAULT '{}'::json NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: event_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_logs_id_seq OWNED BY public.event_logs.id;


--
-- Name: feed_discovery_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feed_discovery_runs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    direction_summary text,
    seed_content_ids json DEFAULT '[]'::json NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at timestamp without time zone,
    error_message text,
    token_input integer,
    token_output integer,
    token_total integer,
    token_usage json,
    duration_ms_total double precision,
    duration_ms_direction double precision,
    duration_ms_lane double precision,
    duration_ms_candidate_extract double precision,
    duration_ms_candidate_validate double precision,
    duration_ms_persist double precision,
    timing json
);


--
-- Name: feed_discovery_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feed_discovery_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feed_discovery_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feed_discovery_runs_id_seq OWNED BY public.feed_discovery_runs.id;


--
-- Name: feed_discovery_suggestions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feed_discovery_suggestions (
    id integer NOT NULL,
    run_id integer NOT NULL,
    user_id integer NOT NULL,
    suggestion_type character varying(50) NOT NULL,
    site_url character varying(2048),
    feed_url character varying(2048) NOT NULL,
    title character varying(500),
    description text,
    channel_id character varying(255),
    playlist_id character varying(255),
    rationale text,
    score double precision,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    config json DEFAULT '{}'::json NOT NULL,
    metadata json DEFAULT '{}'::json NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone,
    item_url character varying(2048)
);


--
-- Name: feed_discovery_suggestions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feed_discovery_suggestions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feed_discovery_suggestions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feed_discovery_suggestions_id_seq OWNED BY public.feed_discovery_suggestions.id;


--
-- Name: learning_deck_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_deck_runs (
    id integer NOT NULL,
    deck_id integer NOT NULL,
    user_id integer NOT NULL,
    status character varying(32) NOT NULL,
    interests_prompt text,
    source_snapshot jsonb NOT NULL,
    timeline jsonb NOT NULL,
    artifact_storage_prefix character varying(2048),
    deck_object_key character varying(2048),
    source_notes_object_key character varying(2048),
    source_notes_html_object_key character varying(2048),
    artifact_object_keys jsonb NOT NULL,
    model_provider character varying(50),
    model_name character varying(100),
    sandbox_provider character varying(50),
    sandbox_id character varying(255),
    agent_log_object_key character varying(2048),
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    llm_task_id integer
);


--
-- Name: learning_deck_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.learning_deck_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: learning_deck_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.learning_deck_runs_id_seq OWNED BY public.learning_deck_runs.id;


--
-- Name: learning_decks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_decks (
    id integer NOT NULL,
    user_id integer NOT NULL,
    source_kind character varying(32) NOT NULL,
    source_identity character varying(512) NOT NULL,
    source_url character varying(2048),
    source_content_id integer,
    source_title character varying(500),
    source_metadata jsonb NOT NULL,
    title character varying(500) NOT NULL,
    latest_successful_run_id integer,
    latest_run_id integer,
    artifact_storage_prefix character varying(2048),
    deck_object_key character varying(2048),
    source_notes_object_key character varying(2048),
    source_notes_html_object_key character varying(2048),
    artifact_object_keys jsonb NOT NULL,
    share_enabled boolean NOT NULL,
    share_token_hash character varying(64),
    share_token_nonce character varying(64),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    deleted_at timestamp without time zone,
    latest_task_id integer,
    latest_successful_task_id integer
);


--
-- Name: learning_decks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.learning_decks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: learning_decks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.learning_decks_id_seq OWNED BY public.learning_decks.id;


--
-- Name: llm_task_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_task_actions (
    id integer NOT NULL,
    llm_task_id integer NOT NULL,
    action_name character varying(128) NOT NULL,
    action_status character varying(32) NOT NULL,
    approval_policy character varying(32) NOT NULL,
    approval_required boolean NOT NULL,
    action_input jsonb NOT NULL,
    action_result jsonb NOT NULL,
    rationale text,
    idempotency_key character varying(512),
    approved_by_user_id integer,
    error_message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    approved_at timestamp without time zone,
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);


--
-- Name: llm_task_actions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_task_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_task_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_task_actions_id_seq OWNED BY public.llm_task_actions.id;


--
-- Name: llm_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_tasks (
    id integer NOT NULL,
    user_id integer NOT NULL,
    task_kind character varying(64) NOT NULL,
    mode character varying(64) NOT NULL,
    workflow_key character varying(128) NOT NULL,
    workflow_version integer NOT NULL,
    workflow_state character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    approval_policy jsonb NOT NULL,
    allowed_actions jsonb NOT NULL,
    tool_policy jsonb NOT NULL,
    vm_namespace character varying(255),
    sandbox_provider character varying(50),
    sandbox_id character varying(255),
    workspace_path character varying(2048),
    shared_workspace_path character varying(2048),
    prompt_pack character varying(255),
    input_json jsonb NOT NULL,
    output_json jsonb NOT NULL,
    artifact_manifest jsonb NOT NULL,
    usage_json jsonb NOT NULL,
    status_history jsonb NOT NULL,
    model_provider character varying(50),
    model_name character varying(100),
    agent_log_object_key character varying(2048),
    error_type character varying(128),
    error_message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    subject_id integer,
    parent_task_id integer
);


--
-- Name: llm_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_tasks_id_seq OWNED BY public.llm_tasks.id;


--
-- Name: vendor_usage_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor_usage_records (
    id integer NOT NULL,
    provider character varying(50) NOT NULL,
    model character varying(255) NOT NULL,
    feature character varying(100) NOT NULL,
    operation character varying(100) NOT NULL,
    source character varying(50),
    request_id character varying(100),
    task_id integer,
    content_id integer,
    session_id integer,
    message_id integer,
    user_id integer,
    input_tokens integer,
    output_tokens integer,
    total_tokens integer,
    cost_usd double precision,
    currency character varying(8) DEFAULT 'USD'::character varying NOT NULL,
    pricing_version character varying(50),
    metadata json DEFAULT '{}'::json NOT NULL,
    created_at timestamp without time zone NOT NULL,
    request_count integer,
    resource_count integer,
    cache_read_tokens integer,
    cache_write_tokens integer
);


--
-- Name: llm_usage_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_usage_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_usage_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_usage_records_id_seq OWNED BY public.vendor_usage_records.id;


--
-- Name: news_item_discussions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_item_discussions (
    id integer NOT NULL,
    news_item_id integer NOT NULL,
    platform character varying(50) NOT NULL,
    external_id character varying(255),
    discussion_url character varying(2048),
    title character varying(500),
    author character varying(255),
    score integer,
    comment_count integer,
    raw_comments_ref json,
    raw_comments_sha256 character varying(64),
    fetched_comment_count integer,
    last_count_checked_at timestamp without time zone,
    last_comments_fetched_at timestamp without time zone,
    next_refresh_after timestamp without time zone,
    summary json,
    summary_status character varying(20) DEFAULT 'not_ready'::character varying NOT NULL,
    summary_version integer,
    summary_model character varying(100),
    summary_generated_at timestamp without time zone,
    last_refresh_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    last_refresh_error text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    summary_input_sha256 character varying(64),
    summary_comment_count integer,
    summary_comment_fingerprints json,
    summary_seen_input_sha256 character varying(64),
    summary_seen_comment_count integer,
    summary_seen_comment_fingerprints json,
    summary_incremental_update_count integer NOT NULL
);


--
-- Name: news_item_discussions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.news_item_discussions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: news_item_discussions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.news_item_discussions_id_seq OWNED BY public.news_item_discussions.id;


--
-- Name: news_item_read_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_item_read_status (
    id integer NOT NULL,
    user_id integer NOT NULL,
    news_item_id integer NOT NULL,
    read_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: news_item_read_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.news_item_read_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: news_item_read_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.news_item_read_status_id_seq OWNED BY public.news_item_read_status.id;


--
-- Name: news_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_items (
    id integer NOT NULL,
    ingest_key character varying(128) NOT NULL,
    visibility_scope character varying(20) NOT NULL,
    owner_user_id integer,
    platform character varying(50),
    source_type character varying(50),
    source_label character varying(255),
    source_external_id character varying(255),
    user_scraper_config_id integer,
    user_integration_connection_id integer,
    canonical_item_url character varying(2048),
    canonical_story_url character varying(2048),
    article_url character varying(2048),
    article_domain character varying(255),
    discussion_url character varying(2048),
    summary_key_points json DEFAULT '[]'::json NOT NULL,
    summary_text text,
    raw_metadata json DEFAULT '{}'::json NOT NULL,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    legacy_content_id integer,
    published_at timestamp without time zone,
    ingested_at timestamp without time zone NOT NULL,
    processed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    representative_news_item_id integer,
    cluster_size integer DEFAULT 1 NOT NULL,
    enrichment_updated_at timestamp without time zone
);


--
-- Name: news_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.news_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: news_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.news_items_id_seq OWNED BY public.news_items.id;


--
-- Name: onboarding_discovery_lanes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onboarding_discovery_lanes (
    id integer NOT NULL,
    run_id integer NOT NULL,
    lane_name character varying(160) NOT NULL,
    goal text,
    target character varying(30),
    status character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    query_count integer DEFAULT 0 NOT NULL,
    completed_queries integer DEFAULT 0 NOT NULL,
    queries json DEFAULT '[]'::json NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: onboarding_discovery_lanes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.onboarding_discovery_lanes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: onboarding_discovery_lanes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.onboarding_discovery_lanes_id_seq OWNED BY public.onboarding_discovery_lanes.id;


--
-- Name: onboarding_discovery_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onboarding_discovery_runs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    topic_summary text,
    inferred_topics json DEFAULT '[]'::json NOT NULL,
    lane_summary text,
    created_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone,
    error_message text
);


--
-- Name: onboarding_discovery_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.onboarding_discovery_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: onboarding_discovery_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.onboarding_discovery_runs_id_seq OWNED BY public.onboarding_discovery_runs.id;


--
-- Name: onboarding_discovery_suggestions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onboarding_discovery_suggestions (
    id integer NOT NULL,
    run_id integer NOT NULL,
    user_id integer NOT NULL,
    suggestion_type character varying(50) NOT NULL,
    site_url character varying(2048),
    feed_url character varying(2048),
    subreddit character varying(255),
    title character varying(500),
    description text,
    rationale text,
    score double precision,
    status character varying(20) DEFAULT 'new'::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: onboarding_discovery_suggestions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.onboarding_discovery_suggestions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: onboarding_discovery_suggestions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.onboarding_discovery_suggestions_id_seq OWNED BY public.onboarding_discovery_suggestions.id;


--
-- Name: onboarding_first_edition_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onboarding_first_edition_runs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    status character varying(16) NOT NULL,
    revision integer NOT NULL,
    started_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone
);


--
-- Name: onboarding_first_edition_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.onboarding_first_edition_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: onboarding_first_edition_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.onboarding_first_edition_runs_id_seq OWNED BY public.onboarding_first_edition_runs.id;


--
-- Name: onboarding_first_edition_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onboarding_first_edition_sources (
    id integer NOT NULL,
    run_id integer NOT NULL,
    source_key character varying(160) NOT NULL,
    display_name character varying(255) NOT NULL,
    source_kind character varying(32) NOT NULL,
    "position" integer NOT NULL,
    status character varying(16) NOT NULL,
    processed_item_count integer NOT NULL,
    completed_at timestamp without time zone
);


--
-- Name: onboarding_first_edition_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.onboarding_first_edition_sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: onboarding_first_edition_sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.onboarding_first_edition_sources_id_seq OWNED BY public.onboarding_first_edition_sources.id;


--
-- Name: processing_task_user_access; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_task_user_access (
    task_id integer NOT NULL,
    user_id integer NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: processing_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_tasks (
    id integer NOT NULL,
    task_type character varying(50) NOT NULL,
    content_id integer,
    payload json DEFAULT '{}'::json,
    status character varying(20) DEFAULT 'pending'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    error_message text,
    retry_count integer DEFAULT 0 NOT NULL,
    queue_name character varying(32) DEFAULT 'content'::character varying NOT NULL,
    available_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    locked_at timestamp without time zone,
    locked_by character varying(100),
    lease_expires_at timestamp without time zone,
    dedupe_key character varying(512),
    lease_token uuid,
    owner_user_id integer,
    CONSTRAINT ck_processing_tasks_lease_token_has_owner CHECK (((lease_token IS NULL) OR ((status IS NOT NULL) AND ((status)::text = 'processing'::text) AND (locked_at IS NOT NULL) AND (locked_by IS NOT NULL) AND (lease_expires_at IS NOT NULL))))
);


--
-- Name: processing_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.processing_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: processing_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.processing_tasks_id_seq OWNED BY public.processing_tasks.id;


--
-- Name: user_api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_api_keys (
    id integer NOT NULL,
    user_id integer NOT NULL,
    key_prefix character varying(64) NOT NULL,
    key_hash character varying(128) NOT NULL,
    created_by_admin_user_id integer,
    last_used_at timestamp without time zone,
    revoked_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: user_api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_api_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_api_keys_id_seq OWNED BY public.user_api_keys.id;


--
-- Name: user_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_feedback (
    id integer NOT NULL,
    user_id integer NOT NULL,
    message text NOT NULL,
    source character varying(64) DEFAULT 'ios_settings'::character varying NOT NULL,
    app_version character varying(64),
    build_number character varying(64),
    platform character varying(64),
    os_version character varying(128),
    device_model character varying(128),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: user_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_feedback_id_seq OWNED BY public.user_feedback.id;


--
-- Name: user_integration_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_integration_connections (
    id integer NOT NULL,
    user_id integer NOT NULL,
    provider character varying(50) NOT NULL,
    provider_user_id character varying(255),
    provider_username character varying(255),
    access_token_encrypted text,
    refresh_token_encrypted text,
    token_expires_at timestamp without time zone,
    scopes json,
    connection_metadata json,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: user_integration_connections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_integration_connections_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_integration_connections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_integration_connections_id_seq OWNED BY public.user_integration_connections.id;


--
-- Name: user_integration_sync_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_integration_sync_state (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    cursor character varying(1024),
    last_synced_item_id character varying(255),
    last_synced_at timestamp without time zone,
    last_status character varying(50),
    last_error text,
    sync_metadata json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: user_integration_sync_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_integration_sync_state_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_integration_sync_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_integration_sync_state_id_seq OWNED BY public.user_integration_sync_state.id;


--
-- Name: user_integration_synced_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_integration_synced_items (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    channel character varying(50) NOT NULL,
    external_item_id character varying(255) NOT NULL,
    content_id integer,
    item_url character varying(2048),
    first_synced_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: user_integration_synced_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_integration_synced_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_integration_synced_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_integration_synced_items_id_seq OWNED BY public.user_integration_synced_items.id;


--
-- Name: user_scraper_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_scraper_configs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    scraper_type character varying(50) NOT NULL,
    display_name character varying(255),
    feed_url character varying(2048),
    config json DEFAULT '{}'::json NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: user_scraper_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_scraper_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_scraper_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_scraper_configs_id_seq OWNED BY public.user_scraper_configs.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    apple_id character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    full_name character varying(255),
    is_admin boolean NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    has_completed_new_user_tutorial boolean DEFAULT false NOT NULL,
    has_completed_live_voice_onboarding boolean DEFAULT false NOT NULL,
    twitter_username character varying(50),
    has_completed_onboarding boolean DEFAULT false NOT NULL,
    council_personas json,
    reading_experience character varying(16) DEFAULT 'briefing'::character varying NOT NULL,
    agent_vm_sandbox_id character varying(255),
    agent_vm_template_revision character varying(255),
    agent_vm_snapshot_id character varying(255),
    agent_vm_snapshot_template_revision character varying(255),
    agent_data_revision bigint DEFAULT '0'::bigint NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: agent_data_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_data_files ALTER COLUMN id SET DEFAULT nextval('public.agent_data_files_id_seq'::regclass);


--
-- Name: agent_vm_system_state id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_vm_system_state ALTER COLUMN id SET DEFAULT nextval('public.agent_vm_system_state_id_seq'::regclass);


--
-- Name: analytics_interactions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_interactions ALTER COLUMN id SET DEFAULT nextval('public.analytics_interactions_id_seq'::regclass);


--
-- Name: audio_episodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_episodes ALTER COLUMN id SET DEFAULT nextval('public.audio_episodes_id_seq'::regclass);


--
-- Name: briefing_lenses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_lenses ALTER COLUMN id SET DEFAULT nextval('public.briefing_lenses_id_seq'::regclass);


--
-- Name: briefing_pending_sources id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_pending_sources ALTER COLUMN id SET DEFAULT nextval('public.briefing_pending_sources_id_seq'::regclass);


--
-- Name: briefing_segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_segments ALTER COLUMN id SET DEFAULT nextval('public.briefing_segments_id_seq'::regclass);


--
-- Name: briefing_states user_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_states ALTER COLUMN user_id SET DEFAULT nextval('public.briefing_states_user_id_seq'::regclass);


--
-- Name: chat_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages ALTER COLUMN id SET DEFAULT nextval('public.chat_messages_id_seq'::regclass);


--
-- Name: chat_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions ALTER COLUMN id SET DEFAULT nextval('public.chat_sessions_id_seq'::regclass);


--
-- Name: cli_link_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_link_sessions ALTER COLUMN id SET DEFAULT nextval('public.cli_link_sessions_id_seq'::regclass);


--
-- Name: content_discussions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_discussions ALTER COLUMN id SET DEFAULT nextval('public.content_discussions_id_seq'::regclass);


--
-- Name: content_knowledge_saves id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_knowledge_saves ALTER COLUMN id SET DEFAULT nextval('public.content_favorites_id_seq'::regclass);


--
-- Name: content_read_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_read_status ALTER COLUMN id SET DEFAULT nextval('public.content_read_status_id_seq'::regclass);


--
-- Name: content_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_status ALTER COLUMN id SET DEFAULT nextval('public.content_status_id_seq'::regclass);


--
-- Name: content_unlikes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unlikes ALTER COLUMN id SET DEFAULT nextval('public.content_unlikes_id_seq'::regclass);


--
-- Name: contents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contents ALTER COLUMN id SET DEFAULT nextval('public.contents_id_seq'::regclass);


--
-- Name: daily_news_digests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_news_digests ALTER COLUMN id SET DEFAULT nextval('public.daily_news_digests_id_seq'::regclass);


--
-- Name: event_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_logs ALTER COLUMN id SET DEFAULT nextval('public.event_logs_id_seq'::regclass);


--
-- Name: feed_discovery_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_discovery_runs ALTER COLUMN id SET DEFAULT nextval('public.feed_discovery_runs_id_seq'::regclass);


--
-- Name: feed_discovery_suggestions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_discovery_suggestions ALTER COLUMN id SET DEFAULT nextval('public.feed_discovery_suggestions_id_seq'::regclass);


--
-- Name: learning_deck_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_deck_runs ALTER COLUMN id SET DEFAULT nextval('public.learning_deck_runs_id_seq'::regclass);


--
-- Name: learning_decks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_decks ALTER COLUMN id SET DEFAULT nextval('public.learning_decks_id_seq'::regclass);


--
-- Name: llm_task_actions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_task_actions ALTER COLUMN id SET DEFAULT nextval('public.llm_task_actions_id_seq'::regclass);


--
-- Name: llm_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_tasks ALTER COLUMN id SET DEFAULT nextval('public.llm_tasks_id_seq'::regclass);


--
-- Name: news_item_discussions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_item_discussions ALTER COLUMN id SET DEFAULT nextval('public.news_item_discussions_id_seq'::regclass);


--
-- Name: news_item_read_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_item_read_status ALTER COLUMN id SET DEFAULT nextval('public.news_item_read_status_id_seq'::regclass);


--
-- Name: news_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_items ALTER COLUMN id SET DEFAULT nextval('public.news_items_id_seq'::regclass);


--
-- Name: onboarding_discovery_lanes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_lanes ALTER COLUMN id SET DEFAULT nextval('public.onboarding_discovery_lanes_id_seq'::regclass);


--
-- Name: onboarding_discovery_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_runs ALTER COLUMN id SET DEFAULT nextval('public.onboarding_discovery_runs_id_seq'::regclass);


--
-- Name: onboarding_discovery_suggestions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_suggestions ALTER COLUMN id SET DEFAULT nextval('public.onboarding_discovery_suggestions_id_seq'::regclass);


--
-- Name: onboarding_first_edition_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_first_edition_runs ALTER COLUMN id SET DEFAULT nextval('public.onboarding_first_edition_runs_id_seq'::regclass);


--
-- Name: onboarding_first_edition_sources id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_first_edition_sources ALTER COLUMN id SET DEFAULT nextval('public.onboarding_first_edition_sources_id_seq'::regclass);


--
-- Name: processing_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_tasks ALTER COLUMN id SET DEFAULT nextval('public.processing_tasks_id_seq'::regclass);


--
-- Name: user_api_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_api_keys ALTER COLUMN id SET DEFAULT nextval('public.user_api_keys_id_seq'::regclass);


--
-- Name: user_feedback id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_feedback ALTER COLUMN id SET DEFAULT nextval('public.user_feedback_id_seq'::regclass);


--
-- Name: user_integration_connections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_connections ALTER COLUMN id SET DEFAULT nextval('public.user_integration_connections_id_seq'::regclass);


--
-- Name: user_integration_sync_state id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_sync_state ALTER COLUMN id SET DEFAULT nextval('public.user_integration_sync_state_id_seq'::regclass);


--
-- Name: user_integration_synced_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_synced_items ALTER COLUMN id SET DEFAULT nextval('public.user_integration_synced_items_id_seq'::regclass);


--
-- Name: user_scraper_configs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scraper_configs ALTER COLUMN id SET DEFAULT nextval('public.user_scraper_configs_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: vendor_usage_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_usage_records ALTER COLUMN id SET DEFAULT nextval('public.llm_usage_records_id_seq'::regclass);


--
-- Name: agent_data_files agent_data_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_data_files
    ADD CONSTRAINT agent_data_files_pkey PRIMARY KEY (id);


--
-- Name: agent_vm_system_state agent_vm_system_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_vm_system_state
    ADD CONSTRAINT agent_vm_system_state_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: analytics_interactions analytics_interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_interactions
    ADD CONSTRAINT analytics_interactions_pkey PRIMARY KEY (id);


--
-- Name: audio_episodes audio_episodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_episodes
    ADD CONSTRAINT audio_episodes_pkey PRIMARY KEY (id);


--
-- Name: briefing_lenses briefing_lenses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_lenses
    ADD CONSTRAINT briefing_lenses_pkey PRIMARY KEY (id);


--
-- Name: briefing_pending_sources briefing_pending_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_pending_sources
    ADD CONSTRAINT briefing_pending_sources_pkey PRIMARY KEY (id);


--
-- Name: briefing_segments briefing_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_segments
    ADD CONSTRAINT briefing_segments_pkey PRIMARY KEY (id);


--
-- Name: briefing_states briefing_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_states
    ADD CONSTRAINT briefing_states_pkey PRIMARY KEY (user_id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_sessions chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: cli_link_sessions cli_link_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_link_sessions
    ADD CONSTRAINT cli_link_sessions_pkey PRIMARY KEY (id);


--
-- Name: consumed_refresh_tokens consumed_refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consumed_refresh_tokens
    ADD CONSTRAINT consumed_refresh_tokens_pkey PRIMARY KEY (token_hash);


--
-- Name: content_bodies content_bodies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_bodies
    ADD CONSTRAINT content_bodies_pkey PRIMARY KEY (content_id, variant);


--
-- Name: content_discussions content_discussions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_discussions
    ADD CONSTRAINT content_discussions_pkey PRIMARY KEY (id);


--
-- Name: content_knowledge_saves content_favorites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_knowledge_saves
    ADD CONSTRAINT content_favorites_pkey PRIMARY KEY (id);


--
-- Name: content_read_status content_read_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_read_status
    ADD CONSTRAINT content_read_status_pkey PRIMARY KEY (id);


--
-- Name: content_status content_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_status
    ADD CONSTRAINT content_status_pkey PRIMARY KEY (id);


--
-- Name: content_unlikes content_unlikes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unlikes
    ADD CONSTRAINT content_unlikes_pkey PRIMARY KEY (id);


--
-- Name: contents contents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contents
    ADD CONSTRAINT contents_pkey PRIMARY KEY (id);


--
-- Name: daily_news_digests daily_news_digests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_news_digests
    ADD CONSTRAINT daily_news_digests_pkey PRIMARY KEY (id);


--
-- Name: event_logs event_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_logs
    ADD CONSTRAINT event_logs_pkey PRIMARY KEY (id);


--
-- Name: feed_discovery_runs feed_discovery_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_discovery_runs
    ADD CONSTRAINT feed_discovery_runs_pkey PRIMARY KEY (id);


--
-- Name: feed_discovery_suggestions feed_discovery_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_discovery_suggestions
    ADD CONSTRAINT feed_discovery_suggestions_pkey PRIMARY KEY (id);


--
-- Name: content_status idx_content_status_user_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_status
    ADD CONSTRAINT idx_content_status_user_content UNIQUE (user_id, content_id);


--
-- Name: learning_deck_runs learning_deck_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_deck_runs
    ADD CONSTRAINT learning_deck_runs_pkey PRIMARY KEY (id);


--
-- Name: learning_decks learning_decks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_decks
    ADD CONSTRAINT learning_decks_pkey PRIMARY KEY (id);


--
-- Name: llm_task_actions llm_task_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_task_actions
    ADD CONSTRAINT llm_task_actions_pkey PRIMARY KEY (id);


--
-- Name: llm_tasks llm_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_tasks
    ADD CONSTRAINT llm_tasks_pkey PRIMARY KEY (id);


--
-- Name: vendor_usage_records llm_usage_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_usage_records
    ADD CONSTRAINT llm_usage_records_pkey PRIMARY KEY (id);


--
-- Name: news_item_discussions news_item_discussions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_item_discussions
    ADD CONSTRAINT news_item_discussions_pkey PRIMARY KEY (id);


--
-- Name: news_item_read_status news_item_read_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_item_read_status
    ADD CONSTRAINT news_item_read_status_pkey PRIMARY KEY (id);


--
-- Name: news_items news_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_items
    ADD CONSTRAINT news_items_pkey PRIMARY KEY (id);


--
-- Name: onboarding_discovery_lanes onboarding_discovery_lanes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_lanes
    ADD CONSTRAINT onboarding_discovery_lanes_pkey PRIMARY KEY (id);


--
-- Name: onboarding_discovery_runs onboarding_discovery_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_runs
    ADD CONSTRAINT onboarding_discovery_runs_pkey PRIMARY KEY (id);


--
-- Name: onboarding_discovery_suggestions onboarding_discovery_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_discovery_suggestions
    ADD CONSTRAINT onboarding_discovery_suggestions_pkey PRIMARY KEY (id);


--
-- Name: onboarding_first_edition_runs onboarding_first_edition_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_first_edition_runs
    ADD CONSTRAINT onboarding_first_edition_runs_pkey PRIMARY KEY (id);


--
-- Name: onboarding_first_edition_sources onboarding_first_edition_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_first_edition_sources
    ADD CONSTRAINT onboarding_first_edition_sources_pkey PRIMARY KEY (id);


--
-- Name: processing_task_user_access processing_task_user_access_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_task_user_access
    ADD CONSTRAINT processing_task_user_access_pkey PRIMARY KEY (task_id, user_id);


--
-- Name: processing_tasks processing_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_tasks
    ADD CONSTRAINT processing_tasks_pkey PRIMARY KEY (id);


--
-- Name: agent_data_files uq_agent_data_files_user_document; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_data_files
    ADD CONSTRAINT uq_agent_data_files_user_document UNIQUE (user_id, document_kind, document_key);


--
-- Name: agent_data_files uq_agent_data_files_user_path; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_data_files
    ADD CONSTRAINT uq_agent_data_files_user_path UNIQUE (user_id, path);


--
-- Name: analytics_interactions uq_analytics_interactions_user_interaction; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_interactions
    ADD CONSTRAINT uq_analytics_interactions_user_interaction UNIQUE (user_id, interaction_id);


--
-- Name: audio_episodes uq_audio_episodes_user_kind_group_chapter; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_episodes
    ADD CONSTRAINT uq_audio_episodes_user_kind_group_chapter UNIQUE (user_id, kind, episode_group_id, chapter_index);


--
-- Name: audio_episodes uq_audio_episodes_user_kind_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_episodes
    ADD CONSTRAINT uq_audio_episodes_user_kind_hash UNIQUE (user_id, kind, input_hash);


--
-- Name: briefing_lenses uq_briefing_lenses_user_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_lenses
    ADD CONSTRAINT uq_briefing_lenses_user_key UNIQUE (user_id, key);


--
-- Name: briefing_pending_sources uq_briefing_pending_sources_user_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_pending_sources
    ADD CONSTRAINT uq_briefing_pending_sources_user_source UNIQUE (user_id, source_kind, source_id);


--
-- Name: content_discussions uq_content_discussions_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_discussions
    ADD CONSTRAINT uq_content_discussions_content UNIQUE (content_id);


--
-- Name: content_knowledge_saves uq_content_knowledge_saves_user_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_knowledge_saves
    ADD CONSTRAINT uq_content_knowledge_saves_user_content UNIQUE (user_id, content_id);


--
-- Name: content_read_status uq_content_read_status_user_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_read_status
    ADD CONSTRAINT uq_content_read_status_user_content UNIQUE (user_id, content_id);


--
-- Name: content_unlikes uq_content_unlikes_user_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unlikes
    ADD CONSTRAINT uq_content_unlikes_user_content UNIQUE (user_id, content_id);


--
-- Name: daily_news_digests uq_daily_news_digests_user_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_news_digests
    ADD CONSTRAINT uq_daily_news_digests_user_date UNIQUE (user_id, local_date);


--
-- Name: feed_discovery_suggestions uq_feed_discovery_user_feed; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_discovery_suggestions
    ADD CONSTRAINT uq_feed_discovery_user_feed UNIQUE (user_id, feed_url);


--
-- Name: news_item_discussions uq_news_item_discussions_news_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_item_discussions
    ADD CONSTRAINT uq_news_item_discussions_news_item UNIQUE (news_item_id);


--
-- Name: news_items uq_news_items_ingest_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_items
    ADD CONSTRAINT uq_news_items_ingest_key UNIQUE (ingest_key);


--
-- Name: news_items uq_news_items_legacy_content_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_items
    ADD CONSTRAINT uq_news_items_legacy_content_id UNIQUE (legacy_content_id);


--
-- Name: onboarding_first_edition_sources uq_onboarding_first_edition_sources_run_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onboarding_first_edition_sources
    ADD CONSTRAINT uq_onboarding_first_edition_sources_run_source UNIQUE (run_id, source_key);


--
-- Name: user_integration_connections uq_provider_provider_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_connections
    ADD CONSTRAINT uq_provider_provider_user UNIQUE (provider, provider_user_id);


--
-- Name: user_integration_sync_state uq_user_integration_sync_connection; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_sync_state
    ADD CONSTRAINT uq_user_integration_sync_connection UNIQUE (connection_id);


--
-- Name: user_integration_synced_items uq_user_integration_synced_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_synced_items
    ADD CONSTRAINT uq_user_integration_synced_item UNIQUE (connection_id, channel, external_item_id);


--
-- Name: user_integration_connections uq_user_provider_connection; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_connections
    ADD CONSTRAINT uq_user_provider_connection UNIQUE (user_id, provider);


--
-- Name: user_scraper_configs uq_user_scraper_feed; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scraper_configs
    ADD CONSTRAINT uq_user_scraper_feed UNIQUE (user_id, scraper_type, feed_url);


--
-- Name: user_api_keys user_api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_api_keys
    ADD CONSTRAINT user_api_keys_pkey PRIMARY KEY (id);


--
-- Name: user_feedback user_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_feedback
    ADD CONSTRAINT user_feedback_pkey PRIMARY KEY (id);


--
-- Name: user_integration_connections user_integration_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_connections
    ADD CONSTRAINT user_integration_connections_pkey PRIMARY KEY (id);


--
-- Name: user_integration_sync_state user_integration_sync_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_sync_state
    ADD CONSTRAINT user_integration_sync_state_pkey PRIMARY KEY (id);


--
-- Name: user_integration_synced_items user_integration_synced_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_integration_synced_items
    ADD CONSTRAINT user_integration_synced_items_pkey PRIMARY KEY (id);


--
-- Name: user_scraper_configs user_scraper_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scraper_configs
    ADD CONSTRAINT user_scraper_configs_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_agent_data_files_user_revision; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_data_files_user_revision ON public.agent_data_files USING btree (user_id, revision);


--
-- Name: idx_analytics_interactions_user_content_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_interactions_user_content_occurred ON public.analytics_interactions USING btree (user_id, content_id, occurred_at);


--
-- Name: idx_analytics_interactions_user_type_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_interactions_user_type_occurred ON public.analytics_interactions USING btree (user_id, interaction_type, occurred_at);


--
-- Name: idx_audio_episodes_user_kind_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audio_episodes_user_kind_status ON public.audio_episodes USING btree (user_id, kind, status);


--
-- Name: idx_briefing_lenses_user_status_position; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_briefing_lenses_user_status_position ON public.briefing_lenses USING btree (user_id, status, "position");


--
-- Name: idx_briefing_pending_sources_user_lens; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_briefing_pending_sources_user_lens ON public.briefing_pending_sources USING btree (user_id, lens_key, enqueued_at);


--
-- Name: idx_briefing_segments_lens_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_briefing_segments_lens_status_created ON public.briefing_segments USING btree (lens_id, status, created_at);


--
-- Name: idx_briefing_segments_user_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_briefing_segments_user_status_created ON public.briefing_segments USING btree (user_id, status, created_at);


--
-- Name: idx_chat_messages_session_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_messages_session_created ON public.chat_messages USING btree (session_id, created_at);


--
-- Name: idx_chat_sessions_content; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_content ON public.chat_sessions USING btree (user_id, content_id);


--
-- Name: idx_chat_sessions_news_item; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_news_item ON public.chat_sessions USING btree (user_id, news_item_id);


--
-- Name: idx_chat_sessions_parent_hidden; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_parent_hidden ON public.chat_sessions USING btree (parent_session_id, is_hidden_from_history);


--
-- Name: idx_chat_sessions_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_user_time ON public.chat_sessions USING btree (user_id, last_message_at);


--
-- Name: idx_checkout; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_checkout ON public.contents USING btree (checked_out_by, checked_out_at);


--
-- Name: idx_cli_link_sessions_status_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cli_link_sessions_status_expires ON public.cli_link_sessions USING btree (status, expires_at);


--
-- Name: idx_consumed_refresh_tokens_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumed_refresh_tokens_expiry ON public.consumed_refresh_tokens USING btree (expires_at);


--
-- Name: idx_consumed_refresh_tokens_replay_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumed_refresh_tokens_replay_expiry ON public.consumed_refresh_tokens USING btree (replay_expires_at);


--
-- Name: idx_content_aggregate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_aggregate ON public.contents USING btree (content_type, is_aggregate);


--
-- Name: idx_content_bodies_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_bodies_content_id ON public.content_bodies USING btree (content_id);


--
-- Name: idx_content_bodies_storage_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_bodies_storage_key ON public.content_bodies USING btree (storage_key);


--
-- Name: idx_content_discussions_fetched_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_discussions_fetched_at ON public.content_discussions USING btree (fetched_at);


--
-- Name: idx_content_discussions_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_discussions_platform ON public.content_discussions USING btree (platform);


--
-- Name: idx_content_discussions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_discussions_status ON public.content_discussions USING btree (status);


--
-- Name: idx_content_read_user_read_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_read_user_read_at ON public.content_read_status USING btree (user_id, read_at);


--
-- Name: idx_content_status_user_status_content; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_status_user_status_content ON public.content_status USING btree (user_id, status, content_id);


--
-- Name: idx_content_type_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_content_type_status ON public.contents USING btree (content_type, status);


--
-- Name: idx_contents_classification_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_classification_status ON public.contents USING btree (classification, status, content_type);


--
-- Name: idx_contents_feed_sort_timestamp_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_feed_sort_timestamp_id ON public.contents USING btree (COALESCE(publication_date, processed_at, created_at) DESC, id DESC);


--
-- Name: idx_contents_search_document_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_search_document_gin ON public.contents USING gin (((((setweight(to_tsvector('english'::regconfig, COALESCE(((content_metadata -> 'summary'::text) ->> 'title'::text), ''::text)), 'A'::"char") || setweight(to_tsvector('english'::regconfig, (COALESCE(title, ''::character varying))::text), 'B'::"char")) || setweight(to_tsvector('english'::regconfig, (COALESCE(source, ''::character varying))::text), 'C'::"char")) || setweight(to_tsvector('english'::regconfig, COALESCE(search_text, ''::text)), 'D'::"char"))));


--
-- Name: idx_contents_source_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_source_trgm ON public.contents USING gin (source public.gin_trgm_ops);


--
-- Name: idx_contents_summary_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_summary_title_trgm ON public.contents USING gin (COALESCE(((content_metadata -> 'summary'::text) ->> 'title'::text), ''::text) public.gin_trgm_ops);


--
-- Name: idx_contents_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contents_title_trgm ON public.contents USING gin (title public.gin_trgm_ops);


--
-- Name: idx_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_created_at ON public.contents USING btree (created_at);


--
-- Name: idx_daily_news_digests_user_local_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_daily_news_digests_user_local_date ON public.daily_news_digests USING btree (user_id, local_date);


--
-- Name: idx_daily_news_digests_user_read_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_daily_news_digests_user_read_at ON public.daily_news_digests USING btree (user_id, read_at);


--
-- Name: idx_event_name_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_name_created ON public.event_logs USING btree (event_name, created_at);


--
-- Name: idx_event_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_status_created ON public.event_logs USING btree (event_type, status, created_at);


--
-- Name: idx_event_type_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_type_created ON public.event_logs USING btree (event_type, created_at);


--
-- Name: idx_feed_discovery_runs_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feed_discovery_runs_user_created ON public.feed_discovery_runs USING btree (user_id, created_at);


--
-- Name: idx_feed_discovery_suggestions_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feed_discovery_suggestions_user_status ON public.feed_discovery_suggestions USING btree (user_id, status);


--
-- Name: idx_learning_deck_runs_deck_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_deck_runs_deck_created ON public.learning_deck_runs USING btree (deck_id, created_at);


--
-- Name: idx_learning_deck_runs_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_deck_runs_user_status ON public.learning_deck_runs USING btree (user_id, status);


--
-- Name: idx_learning_decks_user_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_decks_user_updated ON public.learning_decks USING btree (user_id, updated_at);


--
-- Name: idx_llm_task_actions_task_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_task_actions_task_status ON public.llm_task_actions USING btree (llm_task_id, action_status, created_at);


--
-- Name: idx_llm_tasks_kind_mode_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_tasks_kind_mode_created ON public.llm_tasks USING btree (task_kind, mode, created_at);


--
-- Name: idx_llm_tasks_kind_subject_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_tasks_kind_subject_created ON public.llm_tasks USING btree (task_kind, subject_id, created_at);


--
-- Name: idx_llm_tasks_user_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_tasks_user_status_created ON public.llm_tasks USING btree (user_id, status, created_at);


--
-- Name: idx_llm_tasks_workflow_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_tasks_workflow_state ON public.llm_tasks USING btree (workflow_key, workflow_state);


--
-- Name: idx_news_item_discussions_next_refresh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_item_discussions_next_refresh ON public.news_item_discussions USING btree (next_refresh_after);


--
-- Name: idx_news_item_discussions_platform_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_item_discussions_platform_external ON public.news_item_discussions USING btree (platform, external_id);


--
-- Name: idx_news_item_discussions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_item_discussions_status ON public.news_item_discussions USING btree (last_refresh_status);


--
-- Name: idx_news_item_read_status_news_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_item_read_status_news_item_id ON public.news_item_read_status USING btree (news_item_id);


--
-- Name: idx_news_item_read_status_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_item_read_status_user_id ON public.news_item_read_status USING btree (user_id);


--
-- Name: idx_news_item_read_status_user_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_news_item_read_status_user_item ON public.news_item_read_status USING btree (user_id, news_item_id);


--
-- Name: idx_news_items_article_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_article_title_trgm ON public.news_items USING gin (COALESCE(((raw_metadata -> 'article'::text) ->> 'title'::text), ''::text) public.gin_trgm_ops);


--
-- Name: idx_news_items_owner_ingested; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_owner_ingested ON public.news_items USING btree (owner_user_id, ingested_at);


--
-- Name: idx_news_items_relation_external_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_relation_external_key ON public.news_items USING btree (platform, source_external_id);


--
-- Name: idx_news_items_relation_item_key_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_relation_item_key_hash ON public.news_items USING hash (COALESCE(canonical_item_url, discussion_url));


--
-- Name: idx_news_items_relation_story_key_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_relation_story_key_hash ON public.news_items USING hash (COALESCE(canonical_story_url, article_url));


--
-- Name: idx_news_items_relation_title_document_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_relation_title_document_gin ON public.news_items USING gin ((((setweight(to_tsvector('english'::regconfig, COALESCE(((raw_metadata -> 'summary'::text) ->> 'title'::text), ''::text)), 'A'::"char") || setweight(to_tsvector('english'::regconfig, COALESCE(((raw_metadata -> 'article'::text) ->> 'title'::text), ''::text)), 'A'::"char")) || setweight(to_tsvector('english'::regconfig, COALESCE(((raw_metadata -> 'cluster'::text) ->> 'related_titles'::text), ''::text)), 'B'::"char"))));


--
-- Name: idx_news_items_search_document_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_search_document_gin ON public.news_items USING gin (((((setweight(to_tsvector('english'::regconfig, COALESCE(((raw_metadata -> 'summary'::text) ->> 'title'::text), ''::text)), 'A'::"char") || setweight(to_tsvector('english'::regconfig, COALESCE(((raw_metadata -> 'article'::text) ->> 'title'::text), ''::text)), 'B'::"char")) || setweight(to_tsvector('english'::regconfig, COALESCE(summary_text, ''::text)), 'C'::"char")) || setweight(to_tsvector('english'::regconfig, (((((COALESCE(source_label, ''::character varying))::text || ' '::text) || (COALESCE(article_domain, ''::character varying))::text) || ' '::text) || COALESCE(((raw_metadata -> 'cluster'::text) ->> 'related_titles'::text), ''::text))), 'D'::"char"))));


--
-- Name: idx_news_items_status_ingested; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_status_ingested ON public.news_items USING btree (status, ingested_at);


--
-- Name: idx_news_items_summary_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_summary_title_trgm ON public.news_items USING gin (COALESCE(((raw_metadata -> 'summary'::text) ->> 'title'::text), ''::text) public.gin_trgm_ops);


--
-- Name: idx_news_items_visibility_owner_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_visibility_owner_status ON public.news_items USING btree (visibility_scope, owner_user_id, status);


--
-- Name: idx_news_items_visible_feed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_items_visible_feed ON public.news_items USING btree (visibility_scope, owner_user_id, representative_news_item_id, status, ingested_at);


--
-- Name: idx_onboarding_discovery_lanes_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_onboarding_discovery_lanes_run ON public.onboarding_discovery_lanes USING btree (run_id);


--
-- Name: idx_onboarding_discovery_runs_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_onboarding_discovery_runs_user_created ON public.onboarding_discovery_runs USING btree (user_id, created_at);


--
-- Name: idx_onboarding_discovery_suggestions_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_onboarding_discovery_suggestions_run ON public.onboarding_discovery_suggestions USING btree (run_id);


--
-- Name: idx_onboarding_discovery_suggestions_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_onboarding_discovery_suggestions_user_status ON public.onboarding_discovery_suggestions USING btree (user_id, status);


--
-- Name: idx_onboarding_first_edition_sources_run_position; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_onboarding_first_edition_sources_run_position ON public.onboarding_first_edition_sources USING btree (run_id, "position");


--
-- Name: idx_processing_task_user_access_user_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_processing_task_user_access_user_task ON public.processing_task_user_access USING btree (user_id, task_id);


--
-- Name: idx_task_queue_status_available; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_queue_status_available ON public.processing_tasks USING btree (queue_name, status, retry_count, available_at, created_at, id);


--
-- Name: idx_task_queue_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_queue_status_created ON public.processing_tasks USING btree (queue_name, status, created_at);


--
-- Name: idx_task_status_available; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_status_available ON public.processing_tasks USING btree (status, retry_count, available_at, created_at, id);


--
-- Name: idx_task_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_status_created ON public.processing_tasks USING btree (status, created_at);


--
-- Name: idx_task_status_lease_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_status_lease_expires ON public.processing_tasks USING btree (status, lease_expires_at);


--
-- Name: idx_url_content_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_url_content_type ON public.contents USING btree (url, content_type);


--
-- Name: idx_user_api_keys_prefix_revoked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_api_keys_prefix_revoked ON public.user_api_keys USING btree (key_prefix, revoked_at);


--
-- Name: idx_user_api_keys_user_revoked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_api_keys_user_revoked ON public.user_api_keys USING btree (user_id, revoked_at);


--
-- Name: idx_user_integration_provider_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_integration_provider_active ON public.user_integration_connections USING btree (provider, is_active);


--
-- Name: idx_user_integration_sync_last_synced; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_integration_sync_last_synced ON public.user_integration_sync_state USING btree (last_synced_at);


--
-- Name: idx_user_integration_synced_item_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_integration_synced_item_lookup ON public.user_integration_synced_items USING btree (connection_id, channel, last_seen_at);


--
-- Name: idx_user_scraper_user_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_scraper_user_type ON public.user_scraper_configs USING btree (user_id, scraper_type);


--
-- Name: idx_vendor_usage_content_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vendor_usage_content_created ON public.vendor_usage_records USING btree (content_id, created_at);


--
-- Name: idx_vendor_usage_provider_model_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vendor_usage_provider_model_created ON public.vendor_usage_records USING btree (provider, model, created_at);


--
-- Name: idx_vendor_usage_session_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vendor_usage_session_created ON public.vendor_usage_records USING btree (session_id, created_at);


--
-- Name: idx_vendor_usage_task_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vendor_usage_task_created ON public.vendor_usage_records USING btree (task_id, created_at);


--
-- Name: ix_analytics_interactions_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_analytics_interactions_content_id ON public.analytics_interactions USING btree (content_id);


--
-- Name: ix_analytics_interactions_interaction_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_analytics_interactions_interaction_type ON public.analytics_interactions USING btree (interaction_type);


--
-- Name: ix_analytics_interactions_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_analytics_interactions_occurred_at ON public.analytics_interactions USING btree (occurred_at);


--
-- Name: ix_analytics_interactions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_analytics_interactions_user_id ON public.analytics_interactions USING btree (user_id);


--
-- Name: ix_audio_episodes_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_kind ON public.audio_episodes USING btree (kind);


--
-- Name: ix_audio_episodes_share_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_share_enabled ON public.audio_episodes USING btree (share_enabled);


--
-- Name: ix_audio_episodes_share_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_share_token_hash ON public.audio_episodes USING btree (share_token_hash);


--
-- Name: ix_audio_episodes_source_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_source_content_id ON public.audio_episodes USING btree (source_content_id);


--
-- Name: ix_audio_episodes_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_status ON public.audio_episodes USING btree (status);


--
-- Name: ix_audio_episodes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audio_episodes_user_id ON public.audio_episodes USING btree (user_id);


--
-- Name: ix_briefing_lenses_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_lenses_status ON public.briefing_lenses USING btree (status);


--
-- Name: ix_briefing_lenses_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_lenses_tier ON public.briefing_lenses USING btree (tier);


--
-- Name: ix_briefing_lenses_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_lenses_user_id ON public.briefing_lenses USING btree (user_id);


--
-- Name: ix_briefing_pending_sources_lens_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_pending_sources_lens_key ON public.briefing_pending_sources USING btree (lens_key);


--
-- Name: ix_briefing_pending_sources_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_pending_sources_user_id ON public.briefing_pending_sources USING btree (user_id);


--
-- Name: ix_briefing_segments_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_segments_created_at ON public.briefing_segments USING btree (created_at);


--
-- Name: ix_briefing_segments_lens_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_segments_lens_id ON public.briefing_segments USING btree (lens_id);


--
-- Name: ix_briefing_segments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_segments_status ON public.briefing_segments USING btree (status);


--
-- Name: ix_briefing_segments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_segments_user_id ON public.briefing_segments USING btree (user_id);


--
-- Name: ix_chat_messages_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_messages_session_id ON public.chat_messages USING btree (session_id);


--
-- Name: ix_chat_messages_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_messages_status ON public.chat_messages USING btree (status);


--
-- Name: ix_chat_sessions_active_child_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_active_child_session_id ON public.chat_sessions USING btree (active_child_session_id);


--
-- Name: ix_chat_sessions_branch_start_message_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_branch_start_message_id ON public.chat_sessions USING btree (branch_start_message_id);


--
-- Name: ix_chat_sessions_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_content_id ON public.chat_sessions USING btree (content_id);


--
-- Name: ix_chat_sessions_council_message_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_council_message_id ON public.chat_sessions USING btree (council_message_id);


--
-- Name: ix_chat_sessions_council_persona_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_council_persona_id ON public.chat_sessions USING btree (council_persona_id);


--
-- Name: ix_chat_sessions_last_message_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_last_message_at ON public.chat_sessions USING btree (last_message_at);


--
-- Name: ix_chat_sessions_news_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_news_item_id ON public.chat_sessions USING btree (news_item_id);


--
-- Name: ix_chat_sessions_parent_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_parent_session_id ON public.chat_sessions USING btree (parent_session_id);


--
-- Name: ix_chat_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chat_sessions_user_id ON public.chat_sessions USING btree (user_id);


--
-- Name: ix_cli_link_sessions_approved_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cli_link_sessions_approved_by_user_id ON public.cli_link_sessions USING btree (approved_by_user_id);


--
-- Name: ix_cli_link_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cli_link_sessions_expires_at ON public.cli_link_sessions USING btree (expires_at);


--
-- Name: ix_cli_link_sessions_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_cli_link_sessions_session_id ON public.cli_link_sessions USING btree (session_id);


--
-- Name: ix_cli_link_sessions_user_api_key_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cli_link_sessions_user_api_key_id ON public.cli_link_sessions USING btree (user_api_key_id);


--
-- Name: ix_consumed_refresh_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_consumed_refresh_tokens_user_id ON public.consumed_refresh_tokens USING btree (user_id);


--
-- Name: ix_content_knowledge_saves_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_knowledge_saves_content_id ON public.content_knowledge_saves USING btree (content_id);


--
-- Name: ix_content_knowledge_saves_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_knowledge_saves_user_id ON public.content_knowledge_saves USING btree (user_id);


--
-- Name: ix_content_read_status_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_read_status_content_id ON public.content_read_status USING btree (content_id);


--
-- Name: ix_content_read_status_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_read_status_user_id ON public.content_read_status USING btree (user_id);


--
-- Name: ix_content_status_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_status_content_id ON public.content_status USING btree (content_id);


--
-- Name: ix_content_status_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_status_status ON public.content_status USING btree (status);


--
-- Name: ix_content_status_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_status_user_id ON public.content_status USING btree (user_id);


--
-- Name: ix_content_unlikes_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_unlikes_content_id ON public.content_unlikes USING btree (content_id);


--
-- Name: ix_content_unlikes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_content_unlikes_user_id ON public.content_unlikes USING btree (user_id);


--
-- Name: ix_contents_checked_out_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_checked_out_by ON public.contents USING btree (checked_out_by);


--
-- Name: ix_contents_classification; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_classification ON public.contents USING btree (classification);


--
-- Name: ix_contents_content_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_content_type ON public.contents USING btree (content_type);


--
-- Name: ix_contents_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_platform ON public.contents USING btree (platform);


--
-- Name: ix_contents_publication_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_publication_date ON public.contents USING btree (publication_date);


--
-- Name: ix_contents_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_source ON public.contents USING btree (source);


--
-- Name: ix_contents_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_status ON public.contents USING btree (status);


--
-- Name: ix_daily_news_digests_local_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_daily_news_digests_local_date ON public.daily_news_digests USING btree (local_date);


--
-- Name: ix_daily_news_digests_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_daily_news_digests_user_id ON public.daily_news_digests USING btree (user_id);


--
-- Name: ix_event_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_logs_created_at ON public.event_logs USING btree (created_at);


--
-- Name: ix_event_logs_event_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_logs_event_name ON public.event_logs USING btree (event_name);


--
-- Name: ix_event_logs_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_logs_event_type ON public.event_logs USING btree (event_type);


--
-- Name: ix_event_logs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_logs_status ON public.event_logs USING btree (status);


--
-- Name: ix_feed_discovery_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_runs_status ON public.feed_discovery_runs USING btree (status);


--
-- Name: ix_feed_discovery_runs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_runs_user_id ON public.feed_discovery_runs USING btree (user_id);


--
-- Name: ix_feed_discovery_suggestions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_suggestions_run_id ON public.feed_discovery_suggestions USING btree (run_id);


--
-- Name: ix_feed_discovery_suggestions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_suggestions_status ON public.feed_discovery_suggestions USING btree (status);


--
-- Name: ix_feed_discovery_suggestions_suggestion_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_suggestions_suggestion_type ON public.feed_discovery_suggestions USING btree (suggestion_type);


--
-- Name: ix_feed_discovery_suggestions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_feed_discovery_suggestions_user_id ON public.feed_discovery_suggestions USING btree (user_id);


--
-- Name: ix_learning_deck_runs_deck_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_deck_runs_deck_id ON public.learning_deck_runs USING btree (deck_id);


--
-- Name: ix_learning_deck_runs_llm_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_deck_runs_llm_task_id ON public.learning_deck_runs USING btree (llm_task_id);


--
-- Name: ix_learning_deck_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_deck_runs_status ON public.learning_deck_runs USING btree (status);


--
-- Name: ix_learning_deck_runs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_deck_runs_user_id ON public.learning_deck_runs USING btree (user_id);


--
-- Name: ix_learning_decks_deleted_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_deleted_at ON public.learning_decks USING btree (deleted_at);


--
-- Name: ix_learning_decks_latest_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_latest_run_id ON public.learning_decks USING btree (latest_run_id);


--
-- Name: ix_learning_decks_latest_successful_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_latest_successful_run_id ON public.learning_decks USING btree (latest_successful_run_id);


--
-- Name: ix_learning_decks_latest_successful_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_latest_successful_task_id ON public.learning_decks USING btree (latest_successful_task_id);


--
-- Name: ix_learning_decks_latest_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_latest_task_id ON public.learning_decks USING btree (latest_task_id);


--
-- Name: ix_learning_decks_share_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_share_enabled ON public.learning_decks USING btree (share_enabled);


--
-- Name: ix_learning_decks_share_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_share_token_hash ON public.learning_decks USING btree (share_token_hash);


--
-- Name: ix_learning_decks_source_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_source_content_id ON public.learning_decks USING btree (source_content_id);


--
-- Name: ix_learning_decks_source_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_source_kind ON public.learning_decks USING btree (source_kind);


--
-- Name: ix_learning_decks_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_learning_decks_user_id ON public.learning_decks USING btree (user_id);


--
-- Name: ix_llm_task_actions_action_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_task_actions_action_name ON public.llm_task_actions USING btree (action_name);


--
-- Name: ix_llm_task_actions_action_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_task_actions_action_status ON public.llm_task_actions USING btree (action_status);


--
-- Name: ix_llm_task_actions_approved_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_task_actions_approved_by_user_id ON public.llm_task_actions USING btree (approved_by_user_id);


--
-- Name: ix_llm_task_actions_idempotency_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_task_actions_idempotency_key ON public.llm_task_actions USING btree (idempotency_key);


--
-- Name: ix_llm_task_actions_llm_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_task_actions_llm_task_id ON public.llm_task_actions USING btree (llm_task_id);


--
-- Name: ix_llm_tasks_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_mode ON public.llm_tasks USING btree (mode);


--
-- Name: ix_llm_tasks_parent_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_parent_task_id ON public.llm_tasks USING btree (parent_task_id);


--
-- Name: ix_llm_tasks_sandbox_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_sandbox_id ON public.llm_tasks USING btree (sandbox_id);


--
-- Name: ix_llm_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_status ON public.llm_tasks USING btree (status);


--
-- Name: ix_llm_tasks_subject_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_subject_id ON public.llm_tasks USING btree (subject_id);


--
-- Name: ix_llm_tasks_task_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_task_kind ON public.llm_tasks USING btree (task_kind);


--
-- Name: ix_llm_tasks_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_user_id ON public.llm_tasks USING btree (user_id);


--
-- Name: ix_llm_tasks_vm_namespace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_vm_namespace ON public.llm_tasks USING btree (vm_namespace);


--
-- Name: ix_llm_tasks_workflow_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_workflow_key ON public.llm_tasks USING btree (workflow_key);


--
-- Name: ix_llm_tasks_workflow_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_tasks_workflow_state ON public.llm_tasks USING btree (workflow_state);


--
-- Name: ix_news_items_canonical_story_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_canonical_story_url ON public.news_items USING btree (canonical_story_url);


--
-- Name: ix_news_items_ingest_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_ingest_key ON public.news_items USING btree (ingest_key);


--
-- Name: ix_news_items_ingested_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_ingested_at ON public.news_items USING btree (ingested_at);


--
-- Name: ix_news_items_legacy_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_legacy_content_id ON public.news_items USING btree (legacy_content_id);


--
-- Name: ix_news_items_owner_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_owner_user_id ON public.news_items USING btree (owner_user_id);


--
-- Name: ix_news_items_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_platform ON public.news_items USING btree (platform);


--
-- Name: ix_news_items_processed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_processed_at ON public.news_items USING btree (processed_at);


--
-- Name: ix_news_items_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_published_at ON public.news_items USING btree (published_at);


--
-- Name: ix_news_items_source_external_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_source_external_id ON public.news_items USING btree (source_external_id);


--
-- Name: ix_news_items_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_source_type ON public.news_items USING btree (source_type);


--
-- Name: ix_news_items_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_status ON public.news_items USING btree (status);


--
-- Name: ix_news_items_user_integration_connection_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_user_integration_connection_id ON public.news_items USING btree (user_integration_connection_id);


--
-- Name: ix_news_items_user_scraper_config_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_user_scraper_config_id ON public.news_items USING btree (user_scraper_config_id);


--
-- Name: ix_news_items_visibility_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_news_items_visibility_scope ON public.news_items USING btree (visibility_scope);


--
-- Name: ix_onboarding_discovery_lanes_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_discovery_lanes_status ON public.onboarding_discovery_lanes USING btree (status);


--
-- Name: ix_onboarding_discovery_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_discovery_runs_status ON public.onboarding_discovery_runs USING btree (status);


--
-- Name: ix_onboarding_discovery_runs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_discovery_runs_user_id ON public.onboarding_discovery_runs USING btree (user_id);


--
-- Name: ix_onboarding_discovery_suggestions_suggestion_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_discovery_suggestions_suggestion_type ON public.onboarding_discovery_suggestions USING btree (suggestion_type);


--
-- Name: ix_onboarding_first_edition_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_first_edition_runs_status ON public.onboarding_first_edition_runs USING btree (status);


--
-- Name: ix_onboarding_first_edition_runs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_first_edition_runs_user_id ON public.onboarding_first_edition_runs USING btree (user_id);


--
-- Name: ix_onboarding_first_edition_sources_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_first_edition_sources_run_id ON public.onboarding_first_edition_sources USING btree (run_id);


--
-- Name: ix_onboarding_first_edition_sources_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onboarding_first_edition_sources_status ON public.onboarding_first_edition_sources USING btree (status);


--
-- Name: ix_processing_tasks_available_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_available_at ON public.processing_tasks USING btree (available_at);


--
-- Name: ix_processing_tasks_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_content_id ON public.processing_tasks USING btree (content_id);


--
-- Name: ix_processing_tasks_lease_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_lease_expires_at ON public.processing_tasks USING btree (lease_expires_at);


--
-- Name: ix_processing_tasks_locked_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_locked_by ON public.processing_tasks USING btree (locked_by);


--
-- Name: ix_processing_tasks_owner_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_owner_user_id ON public.processing_tasks USING btree (owner_user_id);


--
-- Name: ix_processing_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_status ON public.processing_tasks USING btree (status);


--
-- Name: ix_processing_tasks_task_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_tasks_task_type ON public.processing_tasks USING btree (task_type);


--
-- Name: ix_user_api_keys_created_by_admin_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_api_keys_created_by_admin_user_id ON public.user_api_keys USING btree (created_by_admin_user_id);


--
-- Name: ix_user_api_keys_key_prefix; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_api_keys_key_prefix ON public.user_api_keys USING btree (key_prefix);


--
-- Name: ix_user_api_keys_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_api_keys_user_id ON public.user_api_keys USING btree (user_id);


--
-- Name: ix_user_feedback_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_feedback_created_at ON public.user_feedback USING btree (created_at);


--
-- Name: ix_user_feedback_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_feedback_user_id ON public.user_feedback USING btree (user_id);


--
-- Name: ix_user_integration_connections_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_connections_is_active ON public.user_integration_connections USING btree (is_active);


--
-- Name: ix_user_integration_connections_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_connections_provider ON public.user_integration_connections USING btree (provider);


--
-- Name: ix_user_integration_connections_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_connections_user_id ON public.user_integration_connections USING btree (user_id);


--
-- Name: ix_user_integration_sync_state_connection_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_sync_state_connection_id ON public.user_integration_sync_state USING btree (connection_id);


--
-- Name: ix_user_integration_synced_items_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_synced_items_channel ON public.user_integration_synced_items USING btree (channel);


--
-- Name: ix_user_integration_synced_items_connection_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_synced_items_connection_id ON public.user_integration_synced_items USING btree (connection_id);


--
-- Name: ix_user_integration_synced_items_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_synced_items_content_id ON public.user_integration_synced_items USING btree (content_id);


--
-- Name: ix_user_integration_synced_items_external_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_integration_synced_items_external_item_id ON public.user_integration_synced_items USING btree (external_item_id);


--
-- Name: ix_user_scraper_configs_scraper_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_scraper_configs_scraper_type ON public.user_scraper_configs USING btree (scraper_type);


--
-- Name: ix_user_scraper_configs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_scraper_configs_user_id ON public.user_scraper_configs USING btree (user_id);


--
-- Name: ix_users_apple_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_apple_id ON public.users USING btree (apple_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_users_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_id ON public.users USING btree (id);


--
-- Name: ix_users_twitter_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_twitter_username ON public.users USING btree (twitter_username);


--
-- Name: ix_vendor_usage_records_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_content_id ON public.vendor_usage_records USING btree (content_id);


--
-- Name: ix_vendor_usage_records_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_created_at ON public.vendor_usage_records USING btree (created_at);


--
-- Name: ix_vendor_usage_records_feature; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_feature ON public.vendor_usage_records USING btree (feature);


--
-- Name: ix_vendor_usage_records_message_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_message_id ON public.vendor_usage_records USING btree (message_id);


--
-- Name: ix_vendor_usage_records_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_model ON public.vendor_usage_records USING btree (model);


--
-- Name: ix_vendor_usage_records_operation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_operation ON public.vendor_usage_records USING btree (operation);


--
-- Name: ix_vendor_usage_records_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_provider ON public.vendor_usage_records USING btree (provider);


--
-- Name: ix_vendor_usage_records_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_request_id ON public.vendor_usage_records USING btree (request_id);


--
-- Name: ix_vendor_usage_records_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_session_id ON public.vendor_usage_records USING btree (session_id);


--
-- Name: ix_vendor_usage_records_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_source ON public.vendor_usage_records USING btree (source);


--
-- Name: ix_vendor_usage_records_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_task_id ON public.vendor_usage_records USING btree (task_id);


--
-- Name: ix_vendor_usage_records_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vendor_usage_records_user_id ON public.vendor_usage_records USING btree (user_id);


--
-- Name: uq_learning_deck_runs_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_learning_deck_runs_user_active ON public.learning_deck_runs USING btree (user_id) WHERE status IN ('queued', 'preparing', 'generating', 'validating', 'publishing');


--
-- Name: uq_learning_decks_user_source_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_learning_decks_user_source_active ON public.learning_decks USING btree (user_id, source_identity) WHERE (deleted_at IS NULL);


--
-- Name: uq_llm_task_actions_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_llm_task_actions_idempotency ON public.llm_task_actions USING btree (llm_task_id, action_name, idempotency_key);


--
-- Name: uq_llm_tasks_learning_deck_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_llm_tasks_learning_deck_user_active ON public.llm_tasks USING btree (user_id) WHERE task_kind = 'learning_deck' AND status IN ('queued', 'preparing', 'running', 'awaiting_approval', 'applying');


--
-- Name: uq_onboarding_first_edition_active_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_onboarding_first_edition_active_user ON public.onboarding_first_edition_runs USING btree (user_id) WHERE ((status)::text = 'active'::text);


--
-- Name: uq_processing_tasks_dedupe_key_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_processing_tasks_dedupe_key_active ON public.processing_tasks USING btree (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'processing');


--
-- Name: consumed_refresh_tokens consumed_refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consumed_refresh_tokens
    ADD CONSTRAINT consumed_refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: content_knowledge_saves content_favorites_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_knowledge_saves
    ADD CONSTRAINT content_favorites_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;


--
-- Name: content_knowledge_saves content_favorites_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_knowledge_saves
    ADD CONSTRAINT content_favorites_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: content_read_status content_read_status_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_read_status
    ADD CONSTRAINT content_read_status_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;


--
-- Name: content_read_status content_read_status_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_read_status
    ADD CONSTRAINT content_read_status_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: content_unlikes content_unlikes_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unlikes
    ADD CONSTRAINT content_unlikes_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;


--
-- Name: content_unlikes content_unlikes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unlikes
    ADD CONSTRAINT content_unlikes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: learning_deck_runs fk_learning_deck_runs_llm_task_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_deck_runs
    ADD CONSTRAINT fk_learning_deck_runs_llm_task_id FOREIGN KEY (llm_task_id) REFERENCES public.llm_tasks(id) ON DELETE SET NULL;


--
-- Name: learning_decks fk_learning_decks_latest_successful_task_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_decks
    ADD CONSTRAINT fk_learning_decks_latest_successful_task_id FOREIGN KEY (latest_successful_task_id) REFERENCES public.llm_tasks(id) ON DELETE SET NULL;


--
-- Name: learning_decks fk_learning_decks_latest_task_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_decks
    ADD CONSTRAINT fk_learning_decks_latest_task_id FOREIGN KEY (latest_task_id) REFERENCES public.llm_tasks(id) ON DELETE SET NULL;


--
-- Name: llm_tasks fk_llm_tasks_parent_task_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_tasks
    ADD CONSTRAINT fk_llm_tasks_parent_task_id FOREIGN KEY (parent_task_id) REFERENCES public.llm_tasks(id) ON DELETE SET NULL;


--
-- Name: processing_tasks fk_processing_tasks_owner_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_tasks
    ADD CONSTRAINT fk_processing_tasks_owner_user_id_users FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vendor_usage_records fk_vendor_usage_records_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_usage_records
    ADD CONSTRAINT fk_vendor_usage_records_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: llm_task_actions llm_task_actions_approved_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_task_actions
    ADD CONSTRAINT llm_task_actions_approved_by_user_id_fkey FOREIGN KEY (approved_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: llm_task_actions llm_task_actions_llm_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_task_actions
    ADD CONSTRAINT llm_task_actions_llm_task_id_fkey FOREIGN KEY (llm_task_id) REFERENCES public.llm_tasks(id) ON DELETE CASCADE;


--
-- Name: llm_tasks llm_tasks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_tasks
    ADD CONSTRAINT llm_tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: processing_task_user_access processing_task_user_access_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_task_user_access
    ADD CONSTRAINT processing_task_user_access_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.processing_tasks(id) ON DELETE CASCADE;


--
-- Name: processing_task_user_access processing_task_user_access_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_task_user_access
    ADD CONSTRAINT processing_task_user_access_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--



-- Required legacy-head marker retained for fresh/adopted catalog parity.
INSERT INTO public.alembic_version (version_num) VALUES ('20260829_02');
