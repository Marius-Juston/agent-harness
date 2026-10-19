# Source coverage (#254)

Every post listed in https://claude.dev/sitemap.xml on 2026-10-19 (16 blog posts), with the round-1 file that reviews it
and how its full text was read. "md copy" = the site's own `https://claude.dev/blog/<slug>.md`; "page text" = the
article text extracted from the HTML with curl. WebFetch returned summaries only, so no review relies on it for a post.

| post | review file (section) | full text read as |
|---|---|---|
| solving-code-review-at-anthropic | r1-blog-review-automations-cloud-mods (1) | md copy, 3,685 words |
| building-effective-agent-automations | r1-blog-review-automations-cloud-mods (2) | md copy, 2,725 words |
| claude-code-in-the-cloud | r1-blog-review-automations-cloud-mods (3) | md copy, 5,070 words |
| getting-started-with-claude-code-mods | r1-blog-review-automations-cloud-mods (4) | md copy, 4,763 words |
| building-with-claude-sonnet-5-5 | r1-blog-sonnet-evals-cost-effort (1) | md copy |
| automating-eval-design-and-hillclimbing | r1-blog-sonnet-evals-cost-effort (2) | md copy |
| what-a-task-costs-on-opus-5-5 | r1-blog-sonnet-evals-cost-effort (3) | md copy |
| spending-your-effort | r1-blog-sonnet-evals-cost-effort (4) | md copy (figures: captions/descriptions only) |
| the-new-rules-of-context-engineering-for-claude-5-generation-models | r1-blog-speed-opus55-context-skills (1) | page text |
| getting-the-most-out-of-opus-5-5 | r1-blog-speed-opus55-context-skills (2) | page text |
| lessons-from-building-claude-code-how-we-use-skills | r1-blog-speed-opus55-context-skills (3) | page text |
| how-we-made-claude-ai-faster | r1-blog-speed-opus55-context-skills (4) | page text |
| a-harness-for-every-task-dynamic-workflows-in-claude-code | r1-blog-workflows-html-caching-seeing (1) | page text |
| lessons-from-building-claude-code-prompt-caching-is-everything | r1-blog-workflows-html-caching-seeing (2) | page text |
| seeing-like-an-agent | r1-blog-workflows-html-caching-seeing (3) | page text |
| using-claude-code-the-unreasonable-effectiveness-of-html | r1-blog-workflows-html-caching-seeing (4) | page text |

anthropic.com/engineering: all 26 posts in its index and sitemap were listed; 22 relevant ones were read in full as
page text, plus 9 Claude Code docs pages (r1-engineering-and-docs). Linked resources that were not fetched are
named in each round-1 file's "not fetched" lines (interactive-figure data, X/Twitter threads, some docs pages).
