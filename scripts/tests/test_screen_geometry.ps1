# test_screen_geometry.ps1 -- red/green matrix for screen_capture.ps1's capture rect.
#
# Covers regression R47 (the off-screen guard rejected EVERY maximized reader) and
# regression R48 (GetWindowRect includes Windows' invisible ~8px resize borders, so
# frames carried strips of whatever sat behind the window). Both are one fix:
# resolve the rect through DWMWA_EXTENDED_FRAME_BOUNDS.
#
# Also covers regression R102: a MINIMIZED reader used to die at the <200x200
# plausibility throw as "implausibly small" (its rect is the taskbar button);
# the script now detects it via IsIconic and names the cause.
#
# It never launches Example Reader and never captures a pixel. It creates its own
# throwaway WinForms window, measures both rects against it, and evaluates the
# old and new guard expressions over those measurements. It also asserts
# statically that screen_capture.ps1 still routes BOTH rect reads (capture rect
# and the per-frame moved-or-resized check) through the same helper -- reading
# one raw and one DWM is the regression that aborts every frame.
#
# Run (Windows, interactive desktop required -- it shows a window for ~1s):
#   powershell -ExecutionPolicy Bypass -File scripts\tests\test_screen_geometry.ps1
#
# Exit 0 = all gated asserts pass. Exit 1 = at least one failed.

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Deliberately distinct type names from screen_capture.ps1's, so loading both in
# one PowerShell session cannot collide (Add-Type refuses to redefine a type).
if (-not ('CaptureGeomTest' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public struct CaptureGeomRect { public int Left, Top, Right, Bottom; }
public class CaptureGeomTest {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out CaptureGeomRect rect);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hWnd, int attr, out CaptureGeomRect rect, int size);
}
'@
}
# The exact message screen_capture.ps1 must throw for a minimized reader (regression R102).
$MinimizedMessage = 'the reader window is minimized -- restore it (maximized is the supported layout) -- nothing was captured'
# Same ordering rule as the script under test: DPI awareness before any rect.
[void][CaptureGeomTest]::SetProcessDPIAware()

$script:failures = 0
$script:warnings = 0

function Assert-True([string]$name, [bool]$condition, [string]$detail) {
    if ($condition) {
        Write-Host "PASS: $name"
    }
    else {
        Write-Host "FAIL: $name -- $detail"
        $script:failures++
    }
}

function Write-Measure([string]$name, [string]$detail) {
    Write-Host "INFO: $name -- $detail"
}

function Write-Warn([string]$name, [string]$detail) {
    Write-Host "WARN: $name -- $detail"
    $script:warnings++
}

function Format-Rect($r) {
    if ($null -eq $r) { return '<null>' }
    "L=$($r.Left) T=$($r.Top) R=$($r.Right) B=$($r.Bottom) ($($r.Right - $r.Left)x$($r.Bottom - $r.Top))"
}

function Get-RawRect([IntPtr]$h) {
    $r = New-Object CaptureGeomRect
    if (-not [CaptureGeomTest]::GetWindowRect($h, [ref]$r)) { return $null }
    return $r
}

# Mirrors Get-VisibleRect in screen_capture.ps1: attribute 9 is
# DWMWA_EXTENDED_FRAME_BOUNDS; $null means the call failed (fail closed).
function Get-DwmRect([IntPtr]$h) {
    $r = New-Object CaptureGeomRect
    $size = [System.Runtime.InteropServices.Marshal]::SizeOf([type]'CaptureGeomRect')
    $hr = [CaptureGeomTest]::DwmGetWindowAttribute($h, 9, [ref]$r, $size)
    if ($hr -ne 0) { return $null }
    return $r
}

# $true = the guard REJECTS the window ("extends off-screen").
function Test-GuardRejects($rect, $left, $top, $right, $bottom) {
    return ($rect.Left -lt $left -or $rect.Top -lt $top -or $rect.Right -gt $right -or $rect.Bottom -gt $bottom)
}

# The pre-R102 abort path: the first message a minimized reader hit was the
# <200x200 plausibility throw. Returns the message it would throw, or $null.
function Get-OldAbortMessage($rect) {
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top
    if ($w -lt 200 -or $h -lt 200) { return "Reader window rect is ${w}x${h} -- implausibly small, refusing to capture." }
    return $null
}

# The post-R102 order screen_capture.ps1 uses: IsIconic is asked BEFORE the rect
# is read, so a minimized reader is named; the size throw stays as backstop.
function Get-NewAbortMessage([IntPtr]$h, $rect) {
    if ([CaptureGeomTest]::IsIconic($h)) { return $MinimizedMessage }
    return (Get-OldAbortMessage $rect)
}

Write-Host "=== screen_capture.ps1 geometry: static asserts ==="

$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'screen_capture.ps1'
Assert-True 'screen_capture.ps1 is where the test expects it' (Test-Path $scriptPath) "not found at $scriptPath"
$src = Get-Content $scriptPath -Raw

Assert-True 'imports DwmGetWindowAttribute from dwmapi.dll' `
    ($src -match 'DllImport\("dwmapi\.dll"\)\]\s*public static extern int DwmGetWindowAttribute') `
    'the P/Invoke for the visible-frame query is gone'
Assert-True 'defines the Get-VisibleRect helper' `
    ($src -match '(?m)^function Get-VisibleRect') `
    'no Get-VisibleRect function'
Assert-True 'queries DWM attribute 9 (DWMWA_EXTENDED_FRAME_BOUNDS)' `
    ($src -match 'DwmGetWindowAttribute\([^,]+, 9,') `
    'attribute number is not 9 -- a different attribute returns a different rect'
Assert-True 'capture rect comes from Get-VisibleRect' `
    ($src -match '\$rect = Get-VisibleRect') `
    'the capture rect is not resolved through the helper'
Assert-True 'per-frame moved-or-resized check uses the SAME helper' `
    ($src -match '\$r2 = Get-VisibleRect') `
    'Test-CaptureGuards reads a different rect source -- every frame would abort as "moved or resized"'
Assert-True 'no GetWindowRect call survives' `
    (-not ($src -match '::GetWindowRect')) `
    'a raw window rect is still being read somewhere -- that rect includes the invisible borders (regression R48)'
Assert-True 'GetWindowRect is no longer imported' `
    (-not ($src -match 'extern bool GetWindowRect')) `
    'dead P/Invoke left behind; the point is that no path can reach the raw rect'
Assert-True 'off-screen guard compares against SM_*VIRTUALSCREEN' `
    (($src -match 'GetSystemMetrics\(76\)') -and ($src -match 'GetSystemMetrics\(79\)')) `
    'guard still uses SystemInformation.VirtualScreen -- a DPI-virtualized value compared against a physical-pixel rect'
Assert-True 'manifest records the rect source' `
    ($src -match "rect_source\s*=\s*'dwm_extended_frame_bounds'") `
    'captured folders stay forensically indistinguishable from pre-fix ones'
Assert-True 'imports IsIconic from user32.dll (regression R102)' `
    ($src -match 'DllImport\("user32\.dll"\)\]\s*public static extern bool IsIconic\(IntPtr') `
    'no P/Invoke for the minimized-window query'
Assert-True 'throws the exact minimized-reader message (regression R102)' `
    ($src.Contains('throw "' + $MinimizedMessage + '"')) `
    'the minimized abort message drifted from the pinned wording'
$iconicAt = $src.IndexOf('::IsIconic($hwnd)')
$sizeThrowAt = $src.IndexOf('throw "Reader window rect is')
Assert-True 'IsIconic is checked BEFORE the <200x200 plausibility throw' `
    (($iconicAt -ge 0) -and ($sizeThrowAt -gt $iconicAt)) `
    "IsIconic at $iconicAt, size throw at $sizeThrowAt -- a minimized reader would still die as 'implausibly small'"
Assert-True 'no restore-it-for-them side effect (ShowWindow / SW_RESTORE absent)' `
    (-not ($src -match 'ShowWindow|SW_RESTORE|SetWindowPos')) `
    'the script un-minimizes the reader itself -- the posture is fail closed (regression R102)'

Write-Host ''
Write-Host "=== live window: measured rects and guard decisions ==="

$form = $null
try {
    $vsLeft = [CaptureGeomTest]::GetSystemMetrics(76)
    $vsTop = [CaptureGeomTest]::GetSystemMetrics(77)
    $vsRight = $vsLeft + [CaptureGeomTest]::GetSystemMetrics(78)
    $vsBottom = $vsTop + [CaptureGeomTest]::GetSystemMetrics(79)
    $wf = [System.Windows.Forms.SystemInformation]::VirtualScreen
    Write-Measure 'virtual screen (SM_*VIRTUALSCREEN, physical)' "L=$vsLeft T=$vsTop R=$vsRight B=$vsBottom"
    Write-Measure 'virtual screen (SystemInformation.VirtualScreen)' "L=$($wf.Left) T=$($wf.Top) R=$($wf.Left + $wf.Width) B=$($wf.Top + $wf.Height)"

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'screen-geometry-test'
    $form.ShowInTaskbar = $false
    $form.StartPosition = 'Manual'
    $form.Size = New-Object System.Drawing.Size(900, 700)
    $form.WindowState = 'Maximized'
    $form.Show()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 500
    [System.Windows.Forms.Application]::DoEvents()
    $h = $form.Handle

    # --- maximized window ---------------------------------------------------
    $raw = Get-RawRect $h
    $dwm = Get-DwmRect $h
    Write-Measure 'maximized GetWindowRect (old source)' (Format-Rect $raw)
    Write-Measure 'maximized DWM extended frame bounds (new source)' (Format-Rect $dwm)

    Assert-True 'DWM query succeeds for a normal top-level window' ($null -ne $dwm) 'DwmGetWindowAttribute returned a non-zero HRESULT'
    if ($null -ne $dwm -and $null -ne $raw) {
        Write-Measure 'invisible-border inset (raw minus DWM, L/T/R/B)' `
            "$($dwm.Left - $raw.Left) / $($dwm.Top - $raw.Top) / $($raw.Right - $dwm.Right) / $($raw.Bottom - $dwm.Bottom)"

        # PREMISE (not gated): on Win10/11 a maximized window's raw rect hangs
        # ~7-9px outside every monitor edge, which is what broke the guard. If
        # a future machine does not reproduce it, the fix is still correct --
        # say so in the PR rather than failing the suite.
        $oldRejects = Test-GuardRejects $raw $wf.Left $wf.Top ($wf.Left + $wf.Width) ($wf.Top + $wf.Height)
        if ($oldRejects) {
            Write-Measure 'RED: old guard (raw rect vs SystemInformation.VirtualScreen)' 'REJECTS the maximized window -- regression R47 reproduced'
        }
        else {
            Write-Warn 'premise did not reproduce' 'the old guard accepted this maximized window on this machine (multi-monitor layout?); the gated asserts below still hold'
        }
        if (($dwm.Left - $raw.Left) -le 0 -and ($raw.Right - $dwm.Right) -le 0) {
            Write-Warn 'no invisible border measured' 'raw and DWM rects agree horizontally -- DWM composition off? regression R48 edge bleed cannot be demonstrated here'
        }
        else {
            Write-Measure 'RED: raw rect exceeds the visible frame' "$($dwm.Left - $raw.Left)px left and $($raw.Right - $dwm.Right)px right of foreign screen would be captured per frame (regression R48)"
        }

        Assert-True 'GREEN: new guard ACCEPTS the maximized window (regression R47)' `
            (-not (Test-GuardRejects $dwm $vsLeft $vsTop $vsRight $vsBottom)) `
            "DWM rect $(Format-Rect $dwm) still reads as off-screen against L=$vsLeft T=$vsTop R=$vsRight B=$vsBottom"
        Assert-True 'GREEN: visible frame is plausible (>=200x200, not DWM garbage)' `
            ((($dwm.Right - $dwm.Left) -ge 200) -and (($dwm.Bottom - $dwm.Top) -ge 200)) `
            "visible frame is $($dwm.Right - $dwm.Left)x$($dwm.Bottom - $dwm.Top)"
        Assert-True 'GREEN: visible frame is no larger than the raw rect' `
            ($dwm.Left -ge $raw.Left -and $dwm.Top -ge $raw.Top -and $dwm.Right -le $raw.Right -and $dwm.Bottom -le $raw.Bottom) `
            'the DWM rect falls outside the window rect -- unexpected, do not capture over it'
    }

    # --- restored window deliberately hanging off the left edge -------------
    $form.WindowState = 'Normal'
    [System.Windows.Forms.Application]::DoEvents()
    $form.Location = New-Object System.Drawing.Point(($vsLeft - 400), ($vsTop + 200))
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.Application]::DoEvents()
    $offRaw = Get-RawRect $h
    $offDwm = Get-DwmRect $h
    Write-Measure 'off-screen restored GetWindowRect' (Format-Rect $offRaw)
    Write-Measure 'off-screen restored DWM extended frame bounds' (Format-Rect $offDwm)
    Assert-True 'GREEN: strictness kept -- new guard REJECTS a half-off-screen window' `
        (($null -ne $offDwm) -and (Test-GuardRejects $offDwm $vsLeft $vsTop $vsRight $vsBottom)) `
        'a window hanging 400px off the left edge was accepted; off-screen regions capture as garbage pixels'

    # --- minimized window (regression R102) --------------------------------------
    $form.Location = New-Object System.Drawing.Point(($vsLeft + 100), ($vsTop + 100))
    $form.WindowState = 'Minimized'
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.Application]::DoEvents()
    $minDwm = Get-DwmRect $h
    Write-Measure 'minimized DWM extended frame bounds' (Format-Rect $minDwm)
    Assert-True 'minimized window reports IsIconic = true' ([CaptureGeomTest]::IsIconic($h)) 'IsIconic returned false for a minimized window'
    Assert-True 'minimized DWM query still succeeds (hr=0) -- DWM is not self-fail-closed here' ($null -ne $minDwm) 'DwmGetWindowAttribute failed for the minimized window'
    if ($null -ne $minDwm) {
        $oldMsg = Get-OldAbortMessage $minDwm
        Assert-True 'RED: old path aborted a minimized reader as "implausibly small"' `
            (($null -ne $oldMsg) -and ($oldMsg -like '*implausibly small*')) `
            "old path message was '$oldMsg' -- the premise (minimized rect < 200x200) did not reproduce"
        $newMsg = Get-NewAbortMessage $h $minDwm
        Assert-True 'GREEN: new path names the minimized reader with the exact message' `
            ($newMsg -eq $MinimizedMessage) `
            "new path message was '$newMsg'"
    }

    # --- restored (maximized) again: the DWM rect path must still pass -------
    $form.WindowState = 'Maximized'
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.Application]::DoEvents()
    $reDwm = Get-DwmRect $h
    Write-Measure 'restored (maximized) DWM extended frame bounds' (Format-Rect $reDwm)
    Assert-True 'restored window reports IsIconic = false' (-not [CaptureGeomTest]::IsIconic($h)) 'IsIconic still true after restore'
    Assert-True 'GREEN: restored window passes the new path (no abort message)' `
        (($null -ne $reDwm) -and ($null -eq (Get-NewAbortMessage $h $reDwm))) `
        "restored window still aborts: '$(Get-NewAbortMessage $h $reDwm)'"
    Assert-True 'GREEN: restored window passes the off-screen guard' `
        (($null -ne $reDwm) -and (-not (Test-GuardRejects $reDwm $vsLeft $vsTop $vsRight $vsBottom))) `
        "restored DWM rect $(Format-Rect $reDwm) reads as off-screen"
}
catch {
    Write-Host "FAIL: live-window checks could not run -- $($_.Exception.Message)"
    Write-Host "      (an interactive desktop session is required; this test shows a real window)"
    $script:failures++
}
finally {
    if ($null -ne $form) {
        $form.Close()
        $form.Dispose()
    }
}

Write-Host ''
if ($script:failures -eq 0) {
    Write-Host "OK -- all gated asserts passed ($script:warnings warning(s))"
    exit 0
}
Write-Host "FAILED -- $script:failures gated assert(s) failed ($script:warnings warning(s))"
exit 1
