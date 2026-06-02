---
id: scripts/ascii_infographic
description: Sectioned prompts for the ASCII infographic generation script.
used_by:
  system: scripts/generate_ascii_infographics.py
  system_description: "System prompt for the ASCII infographic generation utility script."
  user: scripts/generate_ascii_infographics.py:build_ascii_prompt
  user_description: "User prompt template for the ASCII infographic generation utility script."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You are an ASCII artist. You create visual ASCII art illustrations, not text descriptions. Output only ASCII art, no explanations.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Create ASCII ART that visually represents this news article's core concept.

ARTICLE TOPIC: $title
KEY POINTS:
$points_text

YOUR TASK: Draw an ASCII art illustration - NOT text descriptions.
Create a VISUAL PICTURE using ASCII characters that captures the essence of this story.

STRICT RULES:
1. Draw actual ASCII ART - shapes, objects, scenes, symbols
2. Use these characters: / \ | - _ = + * # @ . : ; ' " ^ ~ < > ( ) [ ] { }
3. Maximum 14 lines, 44 characters wide
4. NO sentences or paragraphs - only visual art with minimal labels (1-3 words max)
5. Be creative - draw metaphors, not literal descriptions

GOOD EXAMPLES OF ASCII ART:

Tech/AI topic:
    .---.
   /     \
  | () () |    NEURAL
  |   ^   |    NET
   \ === /
    '---'
  /|||||\

Money/Finance topic:
   $$$$$$$$$$$$
  $$$$   $$$$
 $$$$  $$$$  $$$$     MARKET
  $$$$   $$$$      RISE
   $$$$$$$$$$$$
    |
 __|__

Cloud/Data topic:
    .---.
   (     )
  (       )   DATA
   (     )    FLOW
    '---'
      |
   [_____]

Growth topic:
       *
      /|\
     / | \
    /  |  \    UP
   /   |   \
  /____|____\

Now create ASCII art for the article above. Output ONLY the ASCII art, nothing else:
<!-- /prompt-section -->
