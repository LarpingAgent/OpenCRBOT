# Emulator setup notes

## Google Play Games on PC Developer Emulator

Google's Developer Emulator exposes a normal ADB device at `localhost:6520`. It is the default target for OpenCRBOT's first version; root access is not required for listing devices or capturing screenshots. If it is not listed automatically, run:

```powershell
opencrbot connect localhost:6520
opencrbot devices
```

See Google's [Developer Emulator documentation](https://developer.android.com/games/playgames/pg-emulator).

## Production / consumer client

The upstream [GPGPCPatcher](https://github.com/sekedus/GPGPCPatcher) documents a separate route for enabling ADB and root on Production or Developer images. It changes emulator and service files, requires an elevated PowerShell session and WSL2, and creates a backup directory. The upstream restore commands are `GPGPC-patcher.ps1 -Restore` for Production and `GPGPC-patcher.ps1 -Dev -Restore` for Developer. The guide says updates replace patched files, so its patch has to be reapplied after an update.

OpenCRBOT does not download or run that patcher. A patched client can be selected only after its ADB endpoint appears in `opencrbot devices`.

## Concurrency

Google's documentation says the consumer client and Developer Emulator cannot run at the same time. Close one completely before launching the other.
