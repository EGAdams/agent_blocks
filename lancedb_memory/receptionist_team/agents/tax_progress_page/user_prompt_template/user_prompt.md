The user wants the current 2025 tax progress page built and put on the
dashboard's ADW Generated list. Their words are in the additional
instructions below; you need nothing else from them.

1. Run this with the Bash tool, timeout 300000:

   cd /home/adamsl/rol_finances && \
     PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" uv run adws/adw_tax_progress_page.py \
     --message-out /tmp/tax_progress_page_line.txt

2. Read /tmp/tax_progress_page_line.txt and reply with that line exactly as
   written. If the file is missing or the command failed, say plainly that
   the tax progress page could not be built and give the last error line --
   never make up figures.
