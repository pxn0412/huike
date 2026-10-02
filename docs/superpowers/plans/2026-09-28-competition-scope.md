# Competition identity and scoped answers

Goal: Re-uploading short and official titles for the same edition proposes one competition, while answers only use the selected competition's source text. No track selector is added to the competition page.

1. Add a conservative, edition-aware competition matcher. Show candidate matches during upload confirmation; the user explicitly chooses an existing competition or deliberately creates a new one. Enforce the same decision on the server. Test short/official names and unrelated competitions.
2. Scope local evidence to the selected competition and the track named in the question where the original text clearly identifies a track. Ambiguous track questions keep all relevant alternatives with labels. Test common, AI-only, and PBL-only excerpts.
3. Use only the filtered evidence for website answers. The current shared ADP conversation ignores runtime filter variables, so its unrestricted answer cannot satisfy this requirement. Use the existing grounded-answer client and verify quoted text against retrieved original chunks. Preserve the published ADP app for separate direct access, but do not claim its QR route is isolated.
4. Keep the competition space displaying only its own documents and original files. Check the new behavior with temporary test databases, leaving the freshly cleared HuiKe data untouched.

Implementation targets: `website/store.py`, `website/static/app.js`, `website/server.py`, `website/ai.py`, `website/test_store.py`. Only after this baseline should partial notice replacement be implemented; it requires clause-level effective evidence rather than whole-document disabling.
