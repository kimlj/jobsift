# jobsift: notes for agents

## Showing text the owner will paste

Any message, email or reply the owner will paste somewhere (an onlinejobs.ph message,
an application email, a reply to an employer) is:

1. **Saved** in `C:\Users\kimju\OneDrive\Desktop\applications\resume\`, beside the
   application's resume, never in a scratchpad.
2. **Put on the clipboard from that file** with `Set-Clipboard`: the body only, never
   the `SUBJECT:` line.
3. **Shown in the reply as a plain fenced code block, never a `>` blockquote.** The
   terminal draws a blockquote as a ▎ bar on every line, and the bar is copied along
   with the text: a reply went into Gmail with a bar down its whole left edge on
   30 Sep 2026.

Say in one line that it is on the clipboard, so the owner pastes with Ctrl+V instead of
selecting text in the terminal.
