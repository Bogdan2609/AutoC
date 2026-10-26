param([Parameter(Mandatory=$true)][string]$ProjectDir,[Parameter(Mandatory=$true)][string]$SourceDir)
$ErrorActionPreference='Stop'
$project=(Resolve-Path -LiteralPath $ProjectDir).Path
$source=(Resolve-Path -LiteralPath $SourceDir).Path
$manifestPath=Join-Path $source 'PATCH_MANIFEST_V021.json'
if(!(Test-Path -LiteralPath $manifestPath)){throw 'Missing v0.21 manifest'}
$manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if($manifest.version -ne '0.21.0'){throw 'Wrong v0.21 manifest version'}
$files=@()
foreach($entry in $manifest.files){
    $relative=[string]$entry.path
    if([string]::IsNullOrWhiteSpace($relative) -or $relative.StartsWith('/') -or $relative.Contains('..') -or $relative.Contains(':') -or $relative.Contains('\')){throw "Unsafe file path: $relative"}
    $src=Join-Path $source ($relative.Replace('/','\'))
    if(!(Test-Path -LiteralPath $src)){throw "Missing package file: $relative"}
    if((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$entry.sha256).ToLowerInvariant()){throw "Checksum mismatch: $relative"}
    $files+=,$relative
}
foreach($name in @('compose.yaml','Dockerfile','pyproject.toml','scripts\smoke_v018.py')){
    if(!(Test-Path -LiteralPath (Join-Path $project $name))){throw "Expected v0.18 baseline file not found: $name"}
}

function Patch-MediaCompose([string]$inputYaml){
    $lines=[System.Collections.Generic.List[string]]::new()
    foreach($line in [regex]::Split($inputYaml,'\r?\n')){[void]$lines.Add($line)}
    foreach($service in @('web','worker')){
        $start=-1
        for($i=0;$i -lt $lines.Count;$i++){if($lines[$i] -eq "  ${service}:"){$start=$i;break}}
        if($start -lt 0){throw "Compose service missing: $service"}
        $end=$lines.Count
        for($i=$start+1;$i -lt $lines.Count;$i++){
            if($lines[$i] -match '^(  [A-Za-z0-9_-]+:|[A-Za-z0-9_-]+:)[ \t]*$'){$end=$i;break}
        }
        $mountFound=$false;$volumeIndex=-1
        for($i=$start+1;$i -lt $end;$i++){
            if($lines[$i] -match 'catalog_media:/srv/app/media'){$mountFound=$true}
            if($lines[$i] -match '^    volumes:[ \t]*$'){$volumeIndex=$i}
        }
        if(!$mountFound){
            if($volumeIndex -ge 0){$lines.Insert($volumeIndex+1,'      - catalog_media:/srv/app/media')}
            else{$lines.Insert($start+1,'    volumes:');$lines.Insert($start+2,'      - catalog_media:/srv/app/media')}
        }
    }
    $rootVolume=-1
    for($i=0;$i -lt $lines.Count;$i++){if($lines[$i] -eq 'volumes:'){$rootVolume=$i;break}}
    if($rootVolume -lt 0){throw 'Top-level Compose volumes section missing'}
    $mediaRoot=$false
    for($i=$rootVolume+1;$i -lt $lines.Count;$i++){
        if($lines[$i] -match '^  catalog_media:[ \t]*$'){$mediaRoot=$true;break}
        if($lines[$i] -match '^[A-Za-z0-9_-]+:[ \t]*$'){break}
    }
    if(!$mediaRoot){$lines.Insert($rootVolume+1,'  catalog_media:')}
    return ($lines -join "`n")
}

function Patch-Pyproject([string]$toml){
    # Do not use a non-greedy regex for the TOML array: requirement strings
    # such as sqlalchemy[asyncio] and zeep[async] contain literal ']' characters.
    $newline=if($toml.Contains("`r`n")){"`r`n"}else{"`n"}
    $lines=[System.Collections.Generic.List[string]]::new()
    foreach($line in [regex]::Split($toml,'\r?\n')){[void]$lines.Add($line)}
    $start=-1;$end=-1
    for($i=0;$i -lt $lines.Count;$i++){
        if($lines[$i] -match '^\s*dependencies\s*=\s*\[\s*(?:#.*)?$'){
            if($start -ge 0){throw 'Multiple project dependency arrays found'}
            $start=$i
        }
    }
    if($start -lt 0){throw 'Could not safely identify multiline project dependencies array'}
    for($i=$start+1;$i -lt $lines.Count;$i++){
        if($lines[$i] -match '^\s*\]\s*(?:#.*)?$'){$end=$i;break}
        if($lines[$i] -match '^\['){throw 'Dependency array closing bracket not found before another TOML section'}
    }
    if($end -lt 0){throw 'Could not safely find dependencies array terminator'}
    for($i=$start+1;$i -lt $end;$i++){
        if($lines[$i] -match '(?i)^\s*["'']pillow(?:\b|[<>=!~\[])'){return $toml}
    }
    $last=$end-1
    while($last -gt $start -and $lines[$last] -match '^\s*(?:#.*)?$'){$last--}
    if($last -gt $start -and $lines[$last] -notmatch ',\s*(?:#.*)?$'){
        $m=[regex]::Match($lines[$last],'^([ \t]*["''][^"'']+["''])(\s*(?:#.*)?)$')
        if(!$m.Success){throw 'Cannot safely append missing comma to last existing dependency'}
        $lines[$last]=$m.Groups[1].Value+','+$m.Groups[2].Value
    }
    $lines.Insert($end,'    "pillow>=12.0,<13",')
    return ($lines -join $newline)
}

function Patch-Dockerfile([string]$docker){
    if($docker -match 'chown.*appuser.*\/srv\/app\/media'){return $docker}
    $pattern='(?m)^RUN adduser --disabled-password --gecos .* appuser\s*$'
    if(!([regex]::IsMatch($docker,$pattern))){throw 'Cannot insert persistent-media ownership in customized Dockerfile'}
    return [regex]::Replace($docker,$pattern,{
        param($m) $m.Value+"`nRUN mkdir -p /srv/app/media && chown -R appuser:appuser /srv/app/media"
    },1)
}

# Calculate every mutation before touching the user's source or DB.
$newCompose=Patch-MediaCompose ([IO.File]::ReadAllText((Join-Path $project 'compose.yaml')))
$newDocker=Patch-Dockerfile ([IO.File]::ReadAllText((Join-Path $project 'Dockerfile')))
$newPy=Patch-Pyproject ([IO.File]::ReadAllText((Join-Path $project 'pyproject.toml')))

$backup=Join-Path (Join-Path $HOME 'autoparts-backups') ('autoparts_v021_'+(Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$created=@()
foreach($relative in @($files)+@('compose.yaml','Dockerfile','pyproject.toml')){
    $target=Join-Path $project ($relative.Replace('/','\'))
    if(Test-Path -LiteralPath $target){
        $dest=Join-Path $backup ($relative.Replace('/','\'))
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath $target -Destination $dest -Force
    }else{$created+=,$relative}
}
$changed=$false
Push-Location $project
try{
    Write-Host 'Preflight: checking v0.18 baseline and PostgreSQL revision...'
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v018
    if($LASTEXITCODE -ne 0){throw 'v0.18 read-only preflight failed'}
    # Validate exactly the patched bytes with Python 3.14's TOML parser
    # in the already-running v0.18 web container, BEFORE any backup or mutation.
    $newPyEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($newPy))
    docker compose exec -T web /srv/app/.venv/bin/python -c "import base64,sys,tomllib; data=tomllib.loads(base64.b64decode(sys.argv[1]).decode('utf-8')); assert any(x.lower().startswith('pillow') for x in data['project']['dependencies']); print('v0.21 dependency patch: TOML VALID')" $newPyEncoded
    if($LASTEXITCODE -ne 0){throw 'Patched pyproject.toml is invalid; project unchanged'}
    $rev=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
    if($LASTEXITCODE -ne 0 -or $rev -ne '20261019_0007'){throw "Expected migration 20261019_0007, found '$rev'"}
    Write-Host 'Backing up PostgreSQL before changing any source...'
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f /tmp/autoparts_v021_before.dump
    if($LASTEXITCODE -ne 0){throw 'pg_dump failed'}
    docker compose cp db:/tmp/autoparts_v021_before.dump (Join-Path $backup 'postgres_before_v021.dump')
    if($LASTEXITCODE -ne 0){throw 'Could not retrieve pg_dump'}
    docker compose exec -T db rm -f /tmp/autoparts_v021_before.dump
    if(!(Test-Path -LiteralPath (Join-Path $backup 'postgres_before_v021.dump')) -or (Get-Item (Join-Path $backup 'postgres_before_v021.dump')).Length -lt 1024){throw 'Backup empty'}
    $changed=$true
    foreach($relative in $files){
        $target=Join-Path $project ($relative.Replace('/','\'))
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $source ($relative.Replace('/','\'))) -Destination $target -Force
    }
    [IO.File]::WriteAllText((Join-Path $project 'compose.yaml'),$newCompose,[Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $project 'Dockerfile'),$newDocker,[Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $project 'pyproject.toml'),$newPy,[Text.UTF8Encoding]::new($false))
    docker compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Patched Docker Compose configuration is invalid'}
    Write-Host 'Building v0.21 and running migration 20261019_0008...'
    docker compose up -d --build
    if($LASTEXITCODE -ne 0){throw 'Docker build or migration failed'}
    $rev=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
    if($LASTEXITCODE -ne 0 -or $rev -ne '20261019_0008'){throw "Unexpected migration revision: $rev"}
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v021
    if($LASTEXITCODE -ne 0){throw 'v0.21 web smoke failed'}
    docker compose exec -T worker /srv/app/.venv/bin/python -m scripts.smoke_v021
    if($LASTEXITCODE -ne 0){throw 'v0.21 worker smoke failed'}
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v018
    if($LASTEXITCODE -ne 0){throw 'v0.18 compatibility check failed'}
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v017
    if($LASTEXITCODE -ne 0){throw 'v0.17 compatibility check failed'}
    docker compose ps -a
    Write-Host "v0.21 installed. Alembic: $rev. Source and PostgreSQL backup: $backup"
    Write-Host 'Media live in the persistent Docker volume catalog_media; backing up PostgreSQL alone does not back up uploaded media.'
    Write-Host 'No supplier HTTP, orders, purchases or payments were made by the installer.'
}catch{
    Write-Warning ('v0.21 install failed: '+$_)
    if($changed){
        $safe=$true
        try{
            $rev=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
            if($LASTEXITCODE -ne 0){throw 'Cannot inspect PostgreSQL revision'}
            if($rev -eq '20261019_0008'){
                docker compose stop web worker
                if($LASTEXITCODE -ne 0){throw 'Failed to stop application before rollback'}
                docker compose run --rm migrate uv run --no-dev --no-sync alembic downgrade 20261019_0007
                if($LASTEXITCODE -ne 0){throw 'Migration downgrade failed'}
            }elseif($rev -ne '20261019_0007'){throw "Unexpected migration revision $rev"}
        }catch{
            $safe=$false
            Write-Warning ('Automatic schema downgrade not proven: '+$_)
            Write-Warning 'The new source was retained to avoid a mismatched schema. Use the PostgreSQL backup and inspect manually.'
        }
        if($safe){
            foreach($relative in @($files)+@('compose.yaml','Dockerfile','pyproject.toml')){
                $target=Join-Path $project ($relative.Replace('/','\'))
                $saved=Join-Path $backup ($relative.Replace('/','\'))
                if(Test-Path -LiteralPath $saved){Copy-Item -LiteralPath $saved -Destination $target -Force}
                elseif($created -contains $relative){Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue}
            }
            try{
                docker compose up -d --build
                if($LASTEXITCODE -ne 0){Write-Warning 'Rollback Docker build failed; check containers'}
            }catch{Write-Warning ('Rollback rebuild failed: '+$_)}
            Write-Warning 'Previous source and PostgreSQL migration restored (Docker rebuild attempted). Persistent media volume was not deleted.'
        }
    }
    throw
}finally{Pop-Location}
