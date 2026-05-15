# 可视化仪表板启动脚本windows版本
# 使用方法：.\tests\visualization\run_dashboard.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  日志分析 AI 助手 - 可视化仪表板" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python 环境
Write-Host "[1/5] 检查 Python 环境..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Python 已安装：$pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python 未安装，请先安装 Python 3.8 或更高版本" -ForegroundColor Red
    Write-Host ""
    Write-Host "请从 https://www.python.org/downloads/ 下载并安装 Python" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# 检查虚拟环境是否存在
if (-not (Test-Path ".\venv")) {
    Write-Host ""
    Write-Host "[2/5] 虚拟环境未找到，正在创建..." -ForegroundColor Yellow
    try {
        python -m venv venv
        Write-Host "✓ 虚拟环境创建成功" -ForegroundColor Green
    } catch {
        Write-Host "✗ 虚拟环境创建失败，请检查权限" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[2/5] 虚拟环境已存在" -ForegroundColor Yellow
}

# 激活虚拟环境
Write-Host "[3/5] 激活虚拟环境..." -ForegroundColor Yellow
try {
    & .\venv\Scripts\Activate.ps1
    Write-Host "✓ 虚拟环境激活成功" -ForegroundColor Green
} catch {
    Write-Host "✗ 虚拟环境激活失败" -ForegroundColor Red
    exit 1
}

# 升级 pip
Write-Host "[4/5] 升级 pip..." -ForegroundColor Yellow
try {
    pip install --upgrade pip > $null 2>&1
    Write-Host "✓ pip 升级成功" -ForegroundColor Green
} catch {
    Write-Host "⚠️ pip 升级失败，继续执行" -ForegroundColor Yellow
}

# 安装项目依赖
Write-Host "[5/5] 安装项目依赖..." -ForegroundColor Yellow
if (Test-Path ".\requirements.txt") {
    try {
        pip install -r requirements.txt
        Write-Host "✓ 项目依赖安装完成" -ForegroundColor Green
    } catch {
        Write-Host "✗ 依赖安装失败，请检查错误信息" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "⚠️ requirements.txt 文件未找到，安装基础依赖..." -ForegroundColor Yellow
    try {
        pip install streamlit==1.56.0 fpdf==1.7.2 pandas==2.0.0
        Write-Host "✓ 基础依赖安装完成" -ForegroundColor Green
    } catch {
        Write-Host "✗ 基础依赖安装失败" -ForegroundColor Red
        exit 1
    }
}

# 启动 Streamlit
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  🔄 启动可视化仪表板..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 运行 Streamlit
streamlit run src\visualization\dashboard.py --server.port 8501 --server.address localhost

# 如果运行失败
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "✗ 仪表板运行失败，请检查错误信息" -ForegroundColor Red
    exit 1
}
