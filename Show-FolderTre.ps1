param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    Write-Error "Pfad '$Path' existiert nicht oder ist kein Ordner."
    exit 1
}

function Show-Tree {
    param(
        [string]$BasePath,
        [string]$Indent = ""
    )

    $folders = Get-ChildItem -LiteralPath $BasePath -Directory | Sort-Object Name

    for ($i = 0; $i -lt $folders.Count; $i++) {
        $folder = $folders[$i]
        $isLast = $i -eq $folders.Count - 1

        $branch = if ($isLast) { "└─ " } else { "├─ " }
        Write-Output "$Indent$branch$($folder.Name)"

        $childIndent = $Indent + (if ($isLast) { "   " } else { "│  " })
        Show-Tree -BasePath $folder.FullName -Indent $childIndent
    }
}

$root = (Resolve-Path -LiteralPath $Path).Path
Write-Output $root
Show-Tree -BasePath $root
