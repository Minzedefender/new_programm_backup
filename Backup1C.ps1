[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('Run','AddBase','ListBases','RemoveBase','InitKey','SetSecret','ListSecrets')]
    [string]$Action = 'Run',

    [Parameter()]
    [string[]]$Database,

    [Parameter()]
    [string]$Name,

    [Parameter()]
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.json'),

    [Parameter()]
    [switch]$Force,

    [Parameter()]
    [string]$SecretName,

    [Parameter()]
    [string]$Value
)

$Script:SecretsDirectory = Join-Path $PSScriptRoot 'secrets'
$Script:SecretKeyPath = Join-Path $SecretsDirectory 'key.bin'
$Script:SecretsFilePath = Join-Path $SecretsDirectory 'secrets.json'

function Ensure-SecretsDirectory {
    if (-not (Test-Path -LiteralPath $Script:SecretsDirectory)) {
        New-Item -Path $Script:SecretsDirectory -ItemType Directory -Force | Out-Null
    }
}

function New-SecretKey {
    param(
        [switch]$Force
    )

    Ensure-SecretsDirectory
    if ((Test-Path -LiteralPath $Script:SecretKeyPath) -and -not $Force) {
        throw "Ключ уже существует. Используйте параметр -Force для перезаписи."
    }

    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    [System.IO.File]::WriteAllBytes($Script:SecretKeyPath, $bytes)
    Write-Host "Создан ключ шифрования: $Script:SecretKeyPath"
}

function Get-SecretKey {
    if (-not (Test-Path -LiteralPath $Script:SecretKeyPath)) {
        throw "Ключ шифрования не найден. Выполните InitKey."
    }

    return [System.IO.File]::ReadAllBytes($Script:SecretKeyPath)
}

function ConvertTo-Hashtable {
    param(
        [Parameter(Mandatory = $true)]
        $Object
    )

    if ($null -eq $Object) {
        return @{}
    }

    if ($Object -is [System.Collections.IDictionary]) {
        return $Object
    }

    $hash = @{}
    foreach ($prop in $Object.PSObject.Properties) {
        $hash[$prop.Name] = $prop.Value
    }

    return $hash
}

function Load-Secrets {
    Ensure-SecretsDirectory
    if (-not (Test-Path -LiteralPath $Script:SecretsFilePath)) {
        return @{}
    }

    $raw = Get-Content -Path $Script:SecretsFilePath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @{}
    }

    $obj = $raw | ConvertFrom-Json
    if ($null -eq $obj) {
        return @{}
    }

    return ConvertTo-Hashtable -Object $obj
}

function Save-Secrets {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Secrets
    )

    Ensure-SecretsDirectory
    $json = $Secrets | ConvertTo-Json -Depth 3
    $json | Out-File -FilePath $Script:SecretsFilePath -Encoding UTF8
}

function Convert-SecureStringToPlainText {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.SecureString]$SecureString
    )

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Set-SecretValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter()]
        [string]$PlainValue
    )

    $secureValue = $null
    if ($PlainValue) {
        $secureValue = ConvertTo-SecureString -String $PlainValue -AsPlainText -Force
    }
    else {
        $first = Read-Host "Введите значение для '$Name'" -AsSecureString
        $second = Read-Host "Повторите значение" -AsSecureString
        $firstPlain = Convert-SecureStringToPlainText -SecureString $first
        $secondPlain = Convert-SecureStringToPlainText -SecureString $second
        if ($firstPlain -ne $secondPlain) {
            throw "Введённые значения не совпадают."
        }
        $secureValue = ConvertTo-SecureString -String $firstPlain -AsPlainText -Force
    }

    $key = Get-SecretKey
    $encrypted = $secureValue | ConvertFrom-SecureString -Key $key
    $secrets = Load-Secrets
    $secrets[$Name] = $encrypted
    Save-Secrets -Secrets $secrets
    Write-Host "Секрет '$Name' сохранён."
}

function Get-SecretValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [switch]$AsSecure
    )

    $secrets = Load-Secrets
    if (-not $secrets.ContainsKey($Name)) {
        throw "Секрет '$Name' не найден."
    }

    $key = Get-SecretKey
    $secure = ConvertTo-SecureString -String $secrets[$Name] -Key $key
    if ($AsSecure) {
        return $secure
    }

    return Convert-SecureStringToPlainText -SecureString $secure
}

function List-Secrets {
    $secrets = Load-Secrets
    if ($secrets.Count -eq 0) {
        Write-Host "Секреты не заданы." -ForegroundColor Yellow
        return
    }

    Write-Host "Сохранённые секреты:" -ForegroundColor Cyan
    $secrets.Keys | Sort-Object | ForEach-Object { Write-Host " - $_" }
}

function Get-Config {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return [pscustomobject]@{ Bases = @() }
    }

    $raw = Get-Content -Path $ConfigPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{ Bases = @() }
    }

    $config = $raw | ConvertFrom-Json
    if ($null -eq $config) {
        $config = [pscustomobject]@{ Bases = @() }
    }

    if (-not ($config.PSObject.Properties.Name -contains 'Bases')) {
        $config | Add-Member -MemberType NoteProperty -Name Bases -Value @()
    }

    if ($null -eq $config.Bases) {
        $config.Bases = @()
    }
    else {
        $config.Bases = @($config.Bases)
    }

    return $config
}

function Save-Config {
    param(
        [Parameter(Mandatory = $true)]
        $Config
    )

    $json = $Config | ConvertTo-Json -Depth 6
    $json | Out-File -FilePath $ConfigPath -Encoding UTF8
    Write-Host "Конфигурация сохранена: $ConfigPath"
}

function Ensure-DirectoryExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -ItemType Directory -Force | Out-Null
    }
}

function Add-Base {
    param(
        [string]$Name
    )

    $config = Get-Config
    $bases = @($config.Bases)

    if (-not $Name) {
        $Name = Read-Host "Название базы"
    }

    if (-not $Name) {
        throw "Название базы не может быть пустым."
    }

    $existing = $bases | Where-Object { $_.Name -ieq $Name }
    if ($existing) {
        throw "База '$Name' уже существует."
    }

    $dbPath = Read-Host "Путь к файловой базе (каталог с 1Cv8.1CD)"
    if (-not $dbPath) {
        throw "Необходимо указать путь к базе."
    }

    $designerPath = Read-Host "Полный путь к 1cv8.exe (DESIGNER)"
    if (-not $designerPath) {
        throw "Необходимо указать путь к 1cv8.exe."
    }

    $backupDir = Read-Host "Каталог, где хранить бэкапы"
    if (-not $backupDir) {
        throw "Необходимо указать каталог для бэкапов."
    }

    $retentionInput = Read-Host "Сколько дней хранить бэкапы? (пусто = не удалять)"
    $retention = $null
    if ($retentionInput) {
        try {
            $retention = [int]$retentionInput
        }
        catch {
            throw "Срок хранения должен быть числом."
        }
    }

    $userSecret = Read-Host "Имя секрета с логином 1С (пусто если не нужен)"
    $passwordSecret = Read-Host "Имя секрета с паролем 1С (пусто если не нужен)"

    $uploadAnswer = Read-Host "Загружать бэкапы на Яндекс.Диск? (y/n)"
    $uploadEnabled = $uploadAnswer -match '^[YyДд]'
    $uploadSettings = $null
    if ($uploadEnabled) {
        $tokenSecret = Read-Host "Имя секрета с OAuth токеном API"
        if (-not $tokenSecret) {
            throw "Для загрузки требуется указать секрет с токеном."
        }
        $remoteDir = Read-Host "Удалённая папка (например /Backups/1C)"
        if (-not $remoteDir) {
            $remoteDir = '/' 
        }
        $uploadSettings = [pscustomobject]@{
            Enabled = $true
            RemoteDirectory = $remoteDir
            TokenSecret = $tokenSecret
        }
    }

    $base = [pscustomobject]@{
        Name = $Name
        DatabasePath = $dbPath
        DesignerPath = $designerPath
        BackupDirectory = $backupDir
        RetentionDays = $retention
        UserSecret = if ($userSecret) { $userSecret } else { $null }
        PasswordSecret = if ($passwordSecret) { $passwordSecret } else { $null }
        Upload = $uploadSettings
    }

    $config.Bases = @($bases + $base)
    Save-Config -Config $config
}

function Remove-Base {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $config = Get-Config
    $bases = @($config.Bases)
    if ($bases.Count -eq 0) {
        throw "В конфигурации нет баз."
    }

    $filtered = @()
    $removed = $false
    foreach ($item in $bases) {
        if ($item.Name -ieq $Name) {
            $removed = $true
        }
        else {
            $filtered += $item
        }
    }

    if (-not $removed) {
        throw "База '$Name' не найдена."
    }

    $config.Bases = $filtered
    Save-Config -Config $config
    Write-Host "База '$Name' удалена из конфигурации."
}

function List-Bases {
    $config = Get-Config
    $bases = @($config.Bases)
    if ($bases.Count -eq 0) {
        Write-Host "Базы не настроены." -ForegroundColor Yellow
        return
    }

    $bases | Select-Object Name, DatabasePath, BackupDirectory, RetentionDays | Format-Table -AutoSize
}

function Start-BaseBackup {
    param(
        [Parameter(Mandatory = $true)]
        $Base
    )

    if (-not $Base.DesignerPath) {
        throw "Для базы '$($Base.Name)' не указан путь к 1cv8.exe."
    }
    if (-not (Test-Path -LiteralPath $Base.DesignerPath)) {
        throw "Файл '$($Base.DesignerPath)' не найден."
    }

    if (-not $Base.DatabasePath) {
        throw "Для базы '$($Base.Name)' не указан путь к данным."
    }
    if (-not (Test-Path -LiteralPath $Base.DatabasePath)) {
        throw "Каталог базы '$($Base.DatabasePath)' не найден."
    }

    if (-not $Base.BackupDirectory) {
        throw "Для базы '$($Base.Name)' не указан каталог бэкапов."
    }

    Ensure-DirectoryExists -Path $Base.BackupDirectory
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $fileName = "{0}_{1}.dt" -f $Base.Name, $timestamp
    $targetPath = Join-Path $Base.BackupDirectory $fileName

    $arguments = @(
        'DESIGNER',
        '/DisableStartupDialogs',
        '/DisableStartupMessages',
        '/F', $Base.DatabasePath,
        '/DumpIB', $targetPath
    )

    if ($Base.UserSecret) {
        $user = Get-SecretValue -Name $Base.UserSecret
        $arguments += '/N'
        $arguments += $user
    }

    if ($Base.PasswordSecret) {
        $pass = Get-SecretValue -Name $Base.PasswordSecret
        $arguments += '/P'
        $arguments += $pass
    }

    Write-Host "→ [$($Base.Name)] Создание бэкапа..." -ForegroundColor Cyan
    $process = Start-Process -FilePath $Base.DesignerPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($null -eq $process) {
        throw "Не удалось запустить процесс 1cv8.exe."
    }
    if ($process.ExitCode -ne 0) {
        throw "1cv8.exe завершился с кодом $($process.ExitCode)."
    }

    if (-not (Test-Path -LiteralPath $targetPath)) {
        throw "Бэкап не найден: $targetPath"
    }

    Write-Host "  Бэкап создан: $targetPath" -ForegroundColor Green
    return $targetPath
}

function Cleanup-Backups {
    param(
        [Parameter(Mandatory = $true)]
        $Base
    )

    if (-not $Base.RetentionDays) {
        return
    }

    try {
        [int]$days = $Base.RetentionDays
    }
    catch {
        Write-Warning "RetentionDays для '$($Base.Name)' не является числом. Пропуск очистки."
        return
    }

    if ($days -le 0) {
        return
    }

    $limit = (Get-Date).AddDays(-1 * $days)
    $pattern = "{0}_*.dt" -f $Base.Name
    $files = Get-ChildItem -Path $Base.BackupDirectory -Filter $pattern -File -ErrorAction SilentlyContinue
    foreach ($file in $files) {
        if ($file.LastWriteTime -lt $limit) {
            Remove-Item -LiteralPath $file.FullName -Force
            Write-Host "  Удалён устаревший бэкап: $($file.Name)" -ForegroundColor DarkYellow
        }
    }
}

function Upload-Backup {
    param(
        [Parameter(Mandatory = $true)]
        $Base,

        [Parameter(Mandatory = $true)]
        [string]$FilePath
    )

    if (-not $Base.Upload) {
        return
    }

    $upload = $Base.Upload
    $enabled = $true
    if ($upload.PSObject.Properties.Name -contains 'Enabled') {
        $enabled = [bool]$upload.Enabled
    }

    if (-not $enabled) {
        return
    }

    if (-not $upload.TokenSecret) {
        throw "Для загрузки базы '$($Base.Name)' не указан TokenSecret."
    }

    $token = Get-SecretValue -Name $upload.TokenSecret
    $remoteDir = if ($upload.RemoteDirectory) { $upload.RemoteDirectory } else { '/' }
    $remoteDir = $remoteDir.Trim()
    if ($remoteDir.Length -eq 0) {
        $remoteDir = '/'
    }

    $fileName = Split-Path -Path $FilePath -Leaf
    if ($remoteDir.EndsWith('/')) {
        $remotePath = "$remoteDir$fileName"
    }
    else {
        $remotePath = "$remoteDir/$fileName"
    }

    $remotePath = $remotePath -replace '\\', '/'
    if (-not $remotePath.StartsWith('/')) {
        $remotePath = '/' + $remotePath
    }

    $encodedPath = [uri]::EscapeDataString($remotePath)
    $headers = @{ Authorization = "OAuth $token" }

    Write-Host "  Загрузка в Яндекс.Диск: $remotePath" -ForegroundColor Cyan
    $uploadUrl = "https://cloud-api.yandex.net/v1/disk/resources/upload?path=$encodedPath&overwrite=true"
    $response = Invoke-RestMethod -Method Get -Uri $uploadUrl -Headers $headers -UseBasicParsing -ErrorAction Stop
    if (-not $response.href) {
        throw "API не вернуло ссылку для загрузки."
    }

    Invoke-WebRequest -Method Put -Uri $response.href -InFile $FilePath -Headers $headers -UseBasicParsing -ErrorAction Stop | Out-Null
    Write-Host "  Бэкап загружен в облако." -ForegroundColor Green
}

function Run-Backups {
    param(
        [string[]]$DatabaseNames
    )

    $config = Get-Config
    $bases = @($config.Bases)
    if ($bases.Count -eq 0) {
        throw "Нет ни одной базы в конфигурации."
    }

    if ($DatabaseNames -and $DatabaseNames.Count -gt 0) {
        $selected = @()
        foreach ($name in $DatabaseNames) {
            $match = $bases | Where-Object { $_.Name -ieq $name }
            if ($match) {
                $selected += $match
            }
            else {
                Write-Warning "База '$name' не найдена."
            }
        }

        if ($selected.Count -eq 0) {
            throw "Не найдено ни одной базы по указанным именам."
        }

        $bases = $selected
    }

    $success = $true
    foreach ($base in $bases) {
        try {
            $backupFile = Start-BaseBackup -Base $base
            Cleanup-Backups -Base $base
            try {
                Upload-Backup -Base $base -FilePath $backupFile
            }
            catch {
                $success = $false
                Write-Error "Ошибка загрузки для '$($base.Name)': $_"
            }
        }
        catch {
            $success = $false
            Write-Error "Ошибка обработки базы '$($base.Name)': $_"
        }
    }

    if (-not $success) {
        throw "Одна или несколько операций завершились с ошибками."
    }

    Write-Host "Все бэкапы выполнены успешно." -ForegroundColor Green
}

try {
    switch ($Action) {
        'InitKey' {
            New-SecretKey -Force:$Force
        }
        'SetSecret' {
            $targetName = if ($SecretName) { $SecretName } elseif ($Name) { $Name } else { Read-Host 'Имя секрета' }
            if (-not $targetName) {
                throw "Не указано имя секрета."
            }
            Set-SecretValue -Name $targetName -PlainValue $Value
        }
        'ListSecrets' {
            List-Secrets
        }
        'AddBase' {
            Add-Base -Name $Name
        }
        'ListBases' {
            List-Bases
        }
        'RemoveBase' {
            if (-not $Name) {
                throw "Укажите имя базы с помощью параметра -Name."
            }
            Remove-Base -Name $Name
        }
        'Run' {
            Run-Backups -DatabaseNames $Database
        }
        default {
            throw "Неизвестное действие: $Action"
        }
    }
}
catch {
    Write-Error $_
    exit 1
}