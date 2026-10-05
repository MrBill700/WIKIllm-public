# screen_capture.ps1 -- capture a selected desktop window as a sequence of PNG frames.
#
# Screenshot the selected window -> send an advance key -> repeat. Output is a
# folder of page_NNN.png frames plus a run-manifest.json recording whether the
# run completed. The frames are ingested by reading the images directly (the
# multimodal path); the reader's footer ("N CHAPTER -- x/y", e.g.
# "3 THE GOOD NEWS -- 4/5") is the locator in place of print page numbers. Windows-only -- every other script here is
# portable Python. For DRM-free epubs use extract_chapter.py instead.
#
# Usage (from the vault root; the chapter's first-page footer gives -Pages):
#   powershell -ExecutionPolicy Bypass -File scripts\screen_capture.ps1 -WindowTitle "Reader window title" -Pages 17 -OutDir raw\my-book\chapter-1
#
#   -Pages       frames to capture (required, minimum 1 -- read it off the
#                footer "1 / N").
#   -OutDir      destination; must be empty or absent. Numbering restarts at
#                page_001 every run, so reusing a folder would splice two runs
#                into one plausible-looking sequence. Default: a fresh
#                raw\screen-capture\run-<timestamp>\ under this vault.
#   -DelaySec    seconds between page turns (fractions OK, default 1.0).
#   -WindowTitle target window title prefix (required; no application-specific default).
#   -TurnKey     SendKeys page-turn key (default '{RIGHT}').
#   -MaxTurnRetries  extra page turns to try when a frame comes back identical
#                to the previous one (default 2) before aborting.
#   -MaxStabilizationPolls  captures to wait for two consecutive identical
#                frames after a page changes (default 10) before aborting.
#                Focus and window geometry are checked before every capture.
#
# Browser readers that lazy-load pages (archive.org BookReader in Chrome,
# regression R59): the default settle window (10 polls at 100 ms) can abort with
# "did not stabilize". Recipe -- pass -DelaySec 2 -MaxStabilizationPolls 60
# (the higher cap is free on pages that settle: polling stops at the first stable pair;
# -DelaySec 2 costs +1 s/page) and MAXIMIZE the browser window. Working
# invocation (-WindowTitle = your book's Chrome tab-title prefix):
#   powershell -ExecutionPolicy Bypass -File scripts/screen_capture.ps1 -WindowTitle "Better training for distance runners" -TurnKey "{RIGHT}" -Pages N -DelaySec 2 -MaxStabilizationPolls 60 -OutDir raw/<book>/<block>
# Runs end without a trailing page turn, so after a COMPLETED run the next
# run's first frame repeats the last page. After a "did not stabilize" abort
# the failed frame's turn was already sent, so reader position is not
# guaranteed -- navigate back to the last captured page (e.g. by URL) and
# resume; its first frame then repeats that page. Expected, not a splice;
# confirm the running-head page numbers match before dropping one. BookReader frames are
# images of the printed book, so cite the running-head print page (p.NNN),
# never archive.org's URL page index (offset: printed 182 = n209).
# Full recipe: scripts/README.md.
#
# To abort a run: click any other window. The per-frame focus guard stops the
# run cleanly and the manifest records it as incomplete. There is no other
# failsafe -- Ctrl+C in the console works only because clicking the console IS
# a focus change.
#
# Capture geometry: the region captured is the reader's VISIBLE frame
# (DWM extended frame bounds), not the raw window rect -- that rect includes
# Windows' invisible ~8px resize borders, so every frame carried strips of
# whatever sat behind the window: a privacy leak into raw/, plus a
# stabilization deadlock whenever anything animated sat under a strip
# (regression R48). Four consequences worth knowing before a run:
#   - A MAXIMIZED reader is the supported layout (regression R47) -- the layout
#     _meta/book-scanning.md prescribes. The old guard compared the
#     border-inclusive rect against the screen bounds, which a maximized
#     window always overhangs, so it rejected every maximized reader with
#     "Reader window extends off-screen". A MINIMIZED reader aborts by name
#     ("the reader window is minimized -- restore it ...", regression R102) --
#     the script never restores it for you; it fails closed.
#   - A failed rect query ABORTS the run ("DwmGetWindowAttribute
#     (DWMWA_EXTENDED_FRAME_BOUNDS) failed for the reader window"). There is
#     deliberately no fallback to the raw rect: a bad rect must stop the run,
#     not quietly restore the leak.
#   - Frames are ~18px smaller per axis than captures made before 2026-09, so
#     re-capturing a chapter already in raw/ will NOT be pixel-identical to
#     the frames beside it. run-manifest.json records
#     rect_source: dwm_extended_frame_bounds, so a folder's geometry
#     generation stays checkable after the fact.
#   - Foreign-pixel exposure is gone along the straight edges, and entirely
#     for a maximized reader (maximized windows have square corners). A
#     RESTORED window's visible frame is still the bounding box of a
#     rounded-corner frame, so four ~9px corner arcs can still composite what
#     is behind -- prefer maximized.
#
# Before trusting a full-book capture: verify one mid-book frame shows complete
# right-margin text, and grab the TOC + copyright pages for the citation. See
# scripts/README.md for the full method (incl. trimming overshoot by footer).

param(
    [Parameter(Mandatory = $true)][ValidateRange(1, [int]::MaxValue)][int]$Pages,
    [string]$OutDir = "",
    [ValidateRange(0.0, [double]::MaxValue)][double]$DelaySec = 1.0,
    [string]$WindowTitle = '',
    [string]$TurnKey = '{RIGHT}',
    [ValidateRange(0, [int]::MaxValue)][int]$MaxTurnRetries = 2,
    [ValidateRange(1, [int]::MaxValue)][int]$MaxStabilizationPolls = 10
)

if ([string]::IsNullOrWhiteSpace($WindowTitle)) {
    throw 'Pass -WindowTitle with the target window title prefix.'
}


$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Without DPI awareness, CopyFromScreen on a scaled display grabs only the
# top-left logical-size crop of the physical screen (regression R30). The type
# guard lets the script re-run inside one PowerShell session (Add-Type cannot
# redefine a loaded type) -- which is also why the types are named
# Win32CaptureDwm/CaptureRect rather than the pre-DWM Win32Capture/RECT: a
# session that already loaded the old shape would otherwise keep it and fail
# with "method not found" instead of running the new code (regression R48).
if (-not ('Win32CaptureDwm' -as [type])) {
    Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public struct CaptureRect { public int Left, Top, Right, Bottom; }
public class Win32CaptureDwm {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder buf, int n);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hWnd, int attr, out CaptureRect rect, int size);
}
'@
}
# IsIconic lives in its own type (regression R102) for the same re-run reason: a
# session that already loaded Win32CaptureDwm in its pre-R102 shape would keep
# that shape, and a method added to it would fail with "method not found".
if (-not ('Win32CaptureIconic' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32CaptureIconic {
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
'@
}
if (-not ('Win32DpiAwareness' -as [type])) {
    Add-Type @'
using System.Runtime.InteropServices;
public class Win32DpiAwareness {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool IsProcessDPIAware();
}
'@
}
[void][Win32DpiAwareness]::SetProcessDPIAware()
if (-not [Win32DpiAwareness]::IsProcessDPIAware()) {
    throw "Could not establish DPI awareness -- frames could be cropped on a scaled display. Nothing was captured."
}

function Get-ForegroundInfo {
    $hwnd = [Win32CaptureDwm]::GetForegroundWindow()
    $buf = New-Object System.Text.StringBuilder 512
    [void][Win32CaptureDwm]::GetWindowText($hwnd, $buf, $buf.Capacity)
    [pscustomobject]@{ Hwnd = $hwnd; Title = $buf.ToString() }
}

# The window's VISIBLE frame, via DWMWA_EXTENDED_FRAME_BOUNDS (attribute 9).
# GetWindowRect would return the frame PLUS Windows' invisible ~8px resize
# borders, which breaks capture two ways: CopyFromScreen over that rect bakes
# strips of whatever sits behind the window into every frame -- stabilization
# then deadlocks on anything animated back there, and a run that does complete
# leaks foreign pixels into raw/ (regression R48) -- and a MAXIMIZED window's rect
# always overhangs the monitor by those borders, so the off-screen guard below
# rejected every maximized reader, the very layout the method prescribes
# (regression R47). Returns $null on failure; callers fail closed. There is
# deliberately NO fallback to GetWindowRect: a bad rect must abort the run, not
# quietly restore the leak.
# Ordering is load-bearing: SetProcessDPIAware above must run BEFORE any rect
# is read. DWM always reports physical pixels while a DPI-virtualized process's
# other geometry calls do not, so hoisting this above that call would silently
# mix coordinate spaces.
function Get-VisibleRect([IntPtr]$windowHandle) {
    $r = New-Object CaptureRect
    $size = [System.Runtime.InteropServices.Marshal]::SizeOf([type]'CaptureRect')
    $hr = [Win32CaptureDwm]::DwmGetWindowAttribute($windowHandle, 9, [ref]$r, $size)
    if ($hr -ne 0) { return $null }
    return $r
}

if (-not $OutDir) {
    $vault = Split-Path $PSScriptRoot -Parent
    $OutDir = Join-Path $vault ("raw\screen-capture\run-" + (Get-Date -Format yyyyMMdd-HHmmss))
}
if ((Test-Path $OutDir) -and (Get-ChildItem $OutDir -Force | Select-Object -First 1)) {
    throw "OutDir '$OutDir' is not empty. Page numbering restarts at 001 every run; mixing runs splices two captures into one sequence downstream ingest cannot detect. Use a fresh folder."
}

# Fail closed on focus, twice: AppActivate must succeed, AND the window that
# actually holds focus must carry the expected title (AppActivate matches by
# title prefix and can land on the wrong window). Never capture "whatever is
# foreground" -- that is how a terminal full of private text becomes 110
# "book pages" (regression R32).
$wshell = New-Object -ComObject WScript.Shell
# AppActivate's return value is unreliable -- observed returning false while
# the target window was already foreground -- so it is best-effort only. The
# authoritative fail-closed gate is the foreground-title verification below.
[void]$wshell.AppActivate($WindowTitle)
Start-Sleep -Milliseconds 500
$fg = Get-ForegroundInfo
if (-not $fg.Title.StartsWith($WindowTitle, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Foreground window is '$($fg.Title)', not '$WindowTitle' -- could not bring the reader to focus (is it running with the book open?). Nothing was captured."
}
$hwnd = $fg.Hwnd

# A minimized reader passes the title gate (AppActivate matches it by title)
# but its geometry is the taskbar BUTTON, not a page (regression R102: the 199x34
# "implausibly small" abort was this, not a phantom thumbnail window). Name
# the cause so the operator restores the window instead of hunting a window
# that does not exist. Deliberately NO restore-it-for-them: silently
# un-minimizing a window during an unattended run is a surprise; fail closed.
if ([Win32CaptureIconic]::IsIconic($hwnd)) {
    throw "the reader window is minimized -- restore it (maximized is the supported layout) -- nothing was captured"
}

# Capture only the reader window's visible frame. No full-screen fallback ever:
# leaking the rest of the desktop into the scans is the failure this script
# exists to prevent, so a bad rect aborts instead.
$rect = Get-VisibleRect $hwnd
if ($null -eq $rect) {
    throw "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) failed for the reader window. Nothing was captured."
}
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
# Keeps DWM's degenerate answers out: DWM is not self-fail-closed for a
# minimized window -- it returns hr=0 with the taskbar-BUTTON rect (measured
# 183x34 on Win11 26200), and on other builds with ~(-32000,-32000)
# coordinates. The IsIconic check above names that case first; this
# plausibility throw stays as the backstop for everything else.
if ($w -lt 200 -or $h -lt 200) {
    throw "Reader window rect is ${w}x${h} -- implausibly small, refusing to capture."
}
# SM_X/Y/CX/CYVIRTUALSCREEN (76/77/78/79) rather than
# SystemInformation.VirtualScreen: the rect above is physical pixels, and the
# guard must compare like with like on a scaled display.
$vsLeft = [Win32CaptureDwm]::GetSystemMetrics(76)
$vsTop = [Win32CaptureDwm]::GetSystemMetrics(77)
$vsRight = $vsLeft + [Win32CaptureDwm]::GetSystemMetrics(78)
$vsBottom = $vsTop + [Win32CaptureDwm]::GetSystemMetrics(79)
if ($rect.Left -lt $vsLeft -or $rect.Top -lt $vsTop -or $rect.Right -gt $vsRight -or $rect.Bottom -gt $vsBottom) {
    throw "Reader window extends off-screen -- off-screen regions capture as garbage pixels. Move it fully on-screen. Nothing was captured."
}

New-Item -ItemType Directory -Force $OutDir | Out-Null

# Start-Sleep -Seconds binds Int32 in Windows PowerShell 5.1: -DelaySec 0.5
# would truncate to ZERO delay. Milliseconds keeps fractions meaningful.
$delayMs = [int]($DelaySec * 1000)
$stabilizationPollMs = 100

$manifest = [ordered]@{
    window_title    = $fg.Title
    window_rect     = "$w x $h"
    rect_source     = 'dwm_extended_frame_bounds'
    pages_requested = $Pages
    frames_written  = 0
    turn_retries    = 0
    stabilization_polls = 0
    completed       = $false
    abort_reason    = $null
    started         = (Get-Date -Format o)
    ended           = $null
}

function Save-Frame([string]$path) {
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size($w, $h)))
    $g.Dispose()
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}

function Test-CaptureGuards([int]$frame) {
    $now = Get-ForegroundInfo
    if ($now.Hwnd -ne $hwnd) {
        $manifest.abort_reason = "focus lost at frame $frame (foreground became '$($now.Title)')"
        return $false
    }
    # Must read the rect through the SAME helper as the capture rect above --
    # comparing a raw window rect against DWM visible bounds would differ by
    # the invisible border on every single frame and abort the whole run as
    # "moved or resized".
    $r2 = Get-VisibleRect $hwnd
    if ($null -eq $r2) {
        $manifest.abort_reason = "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) failed at frame $frame"
        return $false
    }
    if ($r2.Left -ne $rect.Left -or $r2.Top -ne $rect.Top -or $r2.Right -ne $rect.Right -or $r2.Bottom -ne $rect.Bottom) {
        $manifest.abort_reason = "reader window moved or resized at frame $frame -- later frames would capture the wrong screen region"
        return $false
    }
    return $true
}

Write-Host "Capturing $Pages frames of '$($fg.Title)' ($w x $h) -> $OutDir"
Write-Host "To abort: click any other window."

$prevHash = $null
try {
    :frames for ($i = 1; $i -le $Pages; $i++) {
        # A page turn can be ignored (reader still rendering, end of book,
        # delay too short); the frame then duplicates the previous one and a
        # "completed" run would silently be missing pages. Byte-identical PNG
        # = no advancement (the reader's footer counter changes every page).
        # Once a change appears, require two consecutive identical captures so
        # an animation or partially rendered page cannot become a frame.
        $final = Join-Path $OutDir ("page_{0:d3}.png" -f $i)
        $candidate = "$final.tmp"
        $poll = "$final.poll.tmp"
        $tries = 0
        while ($true) {
            # Focus can be stolen and geometry can change during either page
            # turn retries or stabilization. Guard every capture, not merely
            # each outer frame.
            if (-not (Test-CaptureGuards $i)) { break frames }
            Remove-Item $candidate -ErrorAction SilentlyContinue
            Save-Frame $candidate
            $hash = (Get-FileHash $candidate -Algorithm MD5).Hash
            if ($null -eq $prevHash -or $hash -ne $prevHash) {
                $stabilized = $false
                for ($pollNumber = 1; $pollNumber -le $MaxStabilizationPolls; $pollNumber++) {
                    Start-Sleep -Milliseconds $stabilizationPollMs
                    if (-not (Test-CaptureGuards $i)) {
                        Remove-Item $candidate -ErrorAction SilentlyContinue
                        Remove-Item $poll -ErrorAction SilentlyContinue
                        break frames
                    }
                    Save-Frame $poll
                    $manifest.stabilization_polls++
                    $pollHash = (Get-FileHash $poll -Algorithm MD5).Hash
                    if ($pollHash -eq $hash) {
                        Remove-Item $poll
                        $stabilized = $true
                        break
                    }
                    Remove-Item $candidate
                    Move-Item $poll $candidate
                    $hash = $pollHash
                }
                if (-not $stabilized) {
                    Remove-Item $candidate -ErrorAction SilentlyContinue
                    Remove-Item $poll -ErrorAction SilentlyContinue
                    $manifest.abort_reason = "frame $i did not stabilize after $MaxStabilizationPolls polls"
                    break frames
                }
                break
            }
            $tries++
            if ($tries -gt $MaxTurnRetries) {
                Remove-Item $candidate
                $manifest.abort_reason = "no page advancement at frame $i after $MaxTurnRetries extra page turns (reader stalled, delay too short, or end of book)"
                break frames
            }
            $manifest.turn_retries++
            if (-not (Test-CaptureGuards $i)) {
                Remove-Item $candidate -ErrorAction SilentlyContinue
                break frames
            }
            [System.Windows.Forms.SendKeys]::SendWait($TurnKey)
            Start-Sleep -Milliseconds $delayMs
        }
        # Frame is fresh and stable: rename into place (atomic, so an
        # interrupted run never leaves a half-written PNG that looks like a
        # page).
        Move-Item -Force $candidate $final
        $prevHash = $hash
        $manifest.frames_written = $i
        if ($i -lt $Pages) {
            [System.Windows.Forms.SendKeys]::SendWait($TurnKey)
            Start-Sleep -Milliseconds $delayMs
        }
    }
    $manifest.completed = ($manifest.frames_written -eq $Pages)
}
finally {
    $manifest.ended = (Get-Date -Format o)
    $manifest | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $OutDir 'run-manifest.json')
    if ($manifest.completed) {
        Write-Host "Done: $($manifest.frames_written) frames -> $OutDir ($($manifest.turn_retries) turn retries)"
    }
    else {
        Write-Host "INCOMPLETE: $($manifest.frames_written)/$Pages frames -> $OutDir"
        if ($manifest.abort_reason) { Write-Host "Reason: $($manifest.abort_reason)" }
    }
}
if (-not $manifest.completed) { exit 1 }
