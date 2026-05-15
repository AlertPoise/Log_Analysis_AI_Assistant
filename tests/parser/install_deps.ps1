# 安装 parsers 模块依赖脚本
# 使用方法: .\install_deps.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Parsers 模块依赖安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python 版本
Write-Host "[1/4] 检查 Python 版本..." -ForegroundColor Yellow
python --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: 未找到 Python，请先安装 Python 3.8+" -ForegroundColor Red
    exit 1
}

# 升级 pip
Write-Host ""
Write-Host "[2/4] 升级 pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# 安装核心依赖
Write-Host ""
Write-Host "[3/4] 安装核心依赖..." -ForegroundColor Yellow
pip install `
    "loguru>=0.7.2,<1.0.0" `
    "pydantic>=2.5.0,<3.0.0" `
    "pydantic-settings>=2.1.0,<3.0.0" `
    "python-dateutil>=2.8.2,<3.0.0" `
    "PyYAML>=6.0.2,<7.0.0"

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ 核心依赖安装成功" -ForegroundColor Green
} else {
    Write-Host "✗ 核心依赖安装失败" -ForegroundColor Red
    exit 1
}

# 询问是否安装可选依赖
Write-Host ""
Write-Host "[4/4] 是否安装可选依赖？" -ForegroundColor Yellow
Write-Host "  1) 是 - 安装所有可选依赖（ClickHouse, ES, Kafka, Streamlit）" -ForegroundColor White
Write-Host "  2) 否 - 仅安装核心依赖" -ForegroundColor White
$choice = Read-Host "请选择 (1/2)"

if ($choice -eq "1") {
    Write-Host ""
    Write-Host "安装可选依赖..." -ForegroundColor Yellow

    pip install `
        "clickhouse-connect>=0.7.0,<1.0.0" `
        "elasticsearch>=8.11.0,<9.0.0" `
        "kafka-python-ng>=2.2.0,<3.0.0" `
        "streamlit>=1.30.0,<2.0.0" `
        "fpdf2>=2.7.0,<3.0.0"

    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ 可选依赖安装成功" -ForegroundColor Green
    } else {
        Write-Host "⚠ 部分可选依赖安装失败，但不影响核心功能" -ForegroundColor Yellow
    }
}

# 验证安装
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  验证安装..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

python -c "
import sys
print(f'Python 版本: {sys.version}')
print()

# 测试核心依赖
core_deps = [
    ('loguru', 'loguru'),
    ('pydantic', 'pydantic'),
    ('pydantic_settings', 'pydantic-settings'),
    ('dateutil', 'python-dateutil'),
    ('yaml', 'PyYAML'),
]

print('核心依赖:')
for module_name, package_name in core_deps:
    try:
        module = __import__(module_name)
        version = getattr(module, '__version__', getattr(module, 'VERSION', 'unknown'))
        print(f'  ✓ {package_name} {version}')
    except ImportError as e:
        print(f'  ✗ {package_name} 未安装: {e}')

print()

# 测试可选依赖
optional_deps = [
    ('clickhouse_connect', 'clickhouse-connect'),
    ('elasticsearch', 'elasticsearch'),
    ('kafka', 'kafka-python-ng'),
    ('streamlit', 'streamlit'),
    ('fpdf', 'fpdf2'),
]

print('可选依赖:')
for module_name, package_name in optional_deps:
    try:
        module = __import__(module_name)
        version = getattr(module, '__version__', 'unknown')
        print(f'  ✓ {package_name} {version}')
    except ImportError:
        print(f'  - {package_name} (未安装)')
"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "运行测试:" -ForegroundColor Yellow
Write-Host "  python -m src.parsers.log_processor" -ForegroundColor White
Write-Host ""
