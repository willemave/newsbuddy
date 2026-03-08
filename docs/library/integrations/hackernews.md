# HackerNews Integration

This document describes the HackerNews processing strategy implementation that fetches and summarizes HN discussions including comments.

## Overview

The HackerNews strategy (`HackerNewsProcessorStrategy`) handles HN item URLs (e.g., `https://news.ycombinator.com/item?id=12345`) by:

1. Fetching the item metadata from the HN Firebase API
2. Downloading the first set of top-level comments
3. If the item links to an external article, fetching that content as well
4. Generating a summary that includes both the article content and key insights from the discussion

## Features

### URL Handling
- Recognizes HN item URLs: `https://news.ycombinator.com/item?id=XXXXX`
- Also supports Firebase API URLs: `https://hacker-news.firebaseio.com/v0/item/XXXXX`

### Content Types Supported
- **Link posts**: Articles submitted to HN with external URLs
- **Text posts**: Ask HN, Show HN, and other text-only posts
- **Job posts**: HN job postings

### Metadata Captured
- `hn_score`: The HN score (points)
- `hn_comments_count`: Number of comments
- `hn_submitter`: Username of the submitter
- `hn_discussion_url`: Link to the HN discussion
- `hn_item_type`: Type of HN item (story, ask, show, job)
- `hn_linked_url`: The external URL (if any)
- `is_hn_text_post`: Boolean indicating if it's a text post

### Comment Processing
- Fetches up to 30 top-level comments by default
- Cleans HTML formatting from comment text
- Formats comments for LLM summarization
- Comments are included in the summary generation

### Summary Generation
The LLM generates summaries that:
- Start with "HN: " prefix to identify HackerNews content
- Include both article summary AND key discussion themes
- Extract quotes from both the article and notable comments
- Blend insights from content and community discussion
- Include a section about the HN community response

## Implementation Details

### API Integration
Uses the official HN Firebase API:
- Base URL: `https://hacker-news.firebaseio.com/v0`
- Item endpoint: `/item/{id}.json`
- No authentication required
- Respects rate limits with timeouts

### Processing Flow
1. Extract item ID from URL
2. Fetch item data from Firebase API
3. Fetch top-level comments concurrently
4. If linked article exists:
   - Determine appropriate strategy (PDF, HTML, etc.)
   - Fetch and extract article content
5. Combine article + comments for summarization
6. Generate structured summary with HN context

### Testing
Comprehensive test coverage includes:
- URL pattern recognition
- API response handling
- Comment formatting
- Different content types (text posts vs links)
- Error handling

## Usage Example

When a HackerNews URL is processed:

```python
# Input URL
url = "https://news.ycombinator.com/item?id=12345"

# The strategy will:
# 1. Fetch HN item data
# 2. Download comments
# 3. Fetch linked article (if any)
# 4. Generate summary combining all sources

# Output includes:
# - Article/post content
# - HN metadata (score, comments count)
# - Summary of key discussion points
# - Notable quotes from comments
```

## Future Enhancements

Potential improvements for future iterations:
- Support for fetching nested comment threads
- Caching of HN API responses
- Real-time updates for scores/comments
- Special handling for popular HN domains
- Integration with HN search API