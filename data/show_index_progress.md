# Show index: progress

The back-of-the-book index for /show-index.html lives in data/show_index.json:
- "entries": heading -> {"kind": "person"|"topic", "eps": [guids], "subs": {subheading: [guids]}, "see": [headings]}
- "reviewed": guids of episodes already read and indexed. The build lists any episode not in it as not yet indexed.

Unconfirmed names (transcript spellings that couldn't be checked) go in data/show_index_unconfirmed.md for Chris to review; they're not in the index.

Rules (Chris, 2026-10-15): read every episode (title, notes, transcript where one exists); index what a listener would come looking for; every real person named gets an entry, surname first, first-name-only people with a short identifier; confirm names against notes, titles, or a reliable source; link episode pages only; prefixes like series: for recurring segments and D&D arcs.

Done: ALL 458 episodes read and recorded. Next: cleanup pass, then build /show-index.html.
