# Recording checklist

## Before the take

- [ ] Use the Gate 3 checkout and demo stack. From the repository root run `make up`; wait for the app at `http://localhost:8501` and API at `http://localhost:8000/health`.
- [ ] Confirm demo mode and database readiness at `/health`. For a Tiger Cloud run, verify the database connection is healthy before recording; do not show or paste the connection string. The reset script intentionally works only with the local Compose database.
- [ ] Reset the take with `scripts/demo_reset.sh`. It removes only demo-trial audit feedback and predictions, restarts API/app to clear transient runs and browser session state, and leaves the seeded data and LLM cache intact.
- [ ] Set the browser viewport to **1920×1080** and browser zoom to **100%**. Maximize the window, hide bookmarks and browser chrome where the recorder permits, and keep the Clinova page in frame.
- [ ] Turn off desktop notifications, close unrelated tabs, and check that no terminal, credentials, or `.env` content will appear in the recording.
- [ ] Open a stopwatch and rehearse the six timed beats once. Allow the demo cache and seeded trial history to finish loading before starting the final take.

## If live mode fails mid-take

Switch to `CLINOVA_MODE=demo` and restart the local Compose stack. The seeded Gate 3 demo serves the same frozen risk, flags, and fixes for NCT03232892; the flow and displayed values are identical. Check the source badge and say “frozen demo cache” if asked. If any panel shows fixture data or a failed service, stop and reset before recording again; never present fixture placeholders as model results.
