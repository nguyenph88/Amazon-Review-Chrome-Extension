# Auto-Amazon-Review-Chrome-Extension
Just a chrome extension review for chrome extension
Edit the env.js with the key

DeepSeek/Gemini drafts are cleaned by the Homelab-Backend dwlai service
(`POST http://10.0.0.119/dwlai/rewrite`, offline mode) before they reach the textarea.
Set `DWLAI_TOKEN` in env.js to the backend `SERVICE_TOKEN`. If the service is unreachable
or flags the rewrite unpublishable, the raw draft is used.
