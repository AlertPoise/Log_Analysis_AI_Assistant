"""
Parsers 模块功能演示测试
将测试结果输出到文件，方便观察效果
"""
import sys
import io
import json
from pathlib import Path
from datetime import datetime

# 设置控制台编码为 UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parsers import (
    JSONParser,
    RegexParser,
    LogparserParser,
    LogProcessor,
    DataCleaner,
    StandardLogSchema,
    COMMON_PATTERNS,
    PREDEFINED_PATTERNS,
)


def test_json_parser(output_file):
    """测试 JSON 解析器并输出结果"""
    print("\n" + "="*60)
    print("测试 1: JSON 解析器")
    print("="*60)

    parser = JSONParser(name="json_test")

    test_logs = [
        '{"timestamp": "2024-01-01T10:00:00Z", "username": "admin", "action": "login", "source_ip": "192.168.1.1", "status": "success"}',
        '{"timestamp": "2024-01-01T10:05:00Z", "username": "zhangsan", "action": "api_call", "source_ip": "10.0.0.5", "method": "GET", "uri": "/api/users"}',
        '{"timestamp": "2024-01-01T10:10:00Z", "username": "lisi", "action": "logout", "source_ip": "172.16.0.10"}',
    ]

    results = []
    for i, log in enumerate(test_logs, 1):
        print(f"\n输入日志 {i}:")
        print(f"  {log}")

        result = parser.parse(log)

        if result:
            print(f"✓ 解析成功")
            print(f"  解析字段: {list(result.keys())}")
            results.append({
                'input': log,
                'output': result,
                'status': 'success'
            })
        else:
            print(f"✗ 解析失败")
            results.append({
                'input': log,
                'output': None,
                'status': 'failed'
            })

    # 写入文件
    output_file.write("\n" + "="*60 + "\n")
    output_file.write("测试 1: JSON 解析器\n")
    output_file.write("="*60 + "\n\n")

    for i, result in enumerate(results, 1):
        output_file.write(f"输入日志 {i}:\n")
        output_file.write(f"  {result['input']}\n\n")
        output_file.write(f"解析状态: {result['status']}\n")
        if result['output']:
            output_file.write(f"解析结果:\n")
            output_file.write(json.dumps(result['output'], ensure_ascii=False, indent=2, default=str))
            output_file.write("\n")
        output_file.write("\n" + "-"*60 + "\n\n")

    print(f"\n✓ JSON 解析器测试完成，共 {len(results)} 条")


def test_regex_parser(output_file):
    """测试正则解析器并输出结果"""
    print("\n" + "="*60)
    print("测试 2: 正则解析器")
    print("="*60)

    test_cases = [
        {
            'name': 'VPN 登录日志',
            'pattern': COMMON_PATTERNS['vpn_login'],
            'log_type': 'vpn',
            'logs': [
                "2024-01-01 10:00:00 LOGIN user=admin ip=192.168.1.1 status=SUCCESS",
                "2024-01-01 10:05:00 LOGOUT user=zhangsan ip=10.0.0.5 status=SUCCESS",
                "2024-01-01 10:10:00 LOGIN user=lisi ip=172.16.0.10 status=FAILED",
            ]
        },
        {
            'name': 'Nginx 访问日志',
            'pattern': COMMON_PATTERNS['nginx_access'],
            'log_type': 'network',
            'logs': [
                '192.168.1.100 - - [01/Jan/2024:12:00:00 +0800] "GET /api/users HTTP/1.1" 200 1234 "-" "Mozilla/5.0"',
                '10.0.0.5 - admin [01/Jan/2024:12:05:00 +0800] "POST /api/login HTTP/1.1" 401 567 "-" "curl/7.68.0"',
            ]
        },
        {
            'name': 'API 调用日志',
            'pattern': COMMON_PATTERNS['api_call'],
            'log_type': 'api',
            'logs': [
                "2024-01-01T12:00:00Z GET /api/v1/users user=admin status=200 response_time=45.5ms",
                "2024-01-01T12:05:00Z POST /api/v1/login user=zhangsan status=401 response_time=120.3ms",
            ]
        },
    ]

    all_results = []

    for test_case in test_cases:
        print(f"\n--- {test_case['name']} ---")

        parser = RegexParser(
            name=f"regex_{test_case['name']}",
            config={
                'pattern': test_case['pattern'],
                'log_type': test_case['log_type'],
            }
        )

        case_results = []
        for i, log in enumerate(test_case['logs'], 1):
            print(f"\n输入日志 {i}:")
            print(f"  {log[:80]}...")

            result = parser.parse(log)

            if result:
                print(f"✓ 解析成功")
                print(f"  解析字段: {list(result.keys())}")
                case_results.append({
                    'input': log,
                    'output': result,
                    'status': 'success'
                })
            else:
                print(f"✗ 解析失败")
                case_results.append({
                    'input': log,
                    'output': None,
                    'status': 'failed'
                })

        all_results.append({
            'case_name': test_case['name'],
            'results': case_results
        })

    # 写入文件
    output_file.write("\n" + "="*60 + "\n")
    output_file.write("测试 2: 正则解析器\n")
    output_file.write("="*60 + "\n\n")

    for case in all_results:
        output_file.write(f"测试场景: {case['case_name']}\n")
        output_file.write("-"*60 + "\n\n")

        for i, result in enumerate(case['results'], 1):
            output_file.write(f"输入日志 {i}:\n")
            output_file.write(f"  {result['input']}\n\n")
            output_file.write(f"解析状态: {result['status']}\n")
            if result['output']:
                output_file.write(f"解析结果:\n")
                output_file.write(json.dumps(result['output'], ensure_ascii=False, indent=2, default=str))
                output_file.write("\n")
            output_file.write("\n" + "-"*60 + "\n\n")

    total = sum(len(case['results']) for case in all_results)
    print(f"\n✓ 正则解析器测试完成，共 {total} 条")


def test_logparser(output_file):
    """测试 Logparser 解析器并输出结果"""
    print("\n" + "="*60)
    print("测试 3: Logparser 解析器")
    print("="*60)

    parser = LogparserParser(name="logparser_test")
    parser.load_patterns(PREDEFINED_PATTERNS)

    test_logs = [
        "2024-01-01 10:00:00 LOGIN user=admin ip=192.168.1.1 status=SUCCESS",
        '192.168.1.100 - - [01/Jan/2024:12:00:00 +0800] "GET /api/users HTTP/1.1" 200 1234 "-" "Mozilla/5.0"',
        "2024-01-01T12:00:00Z GET /api/v1/users user=admin status=200 response_time=45.5ms",
    ]

    results = []
    for i, log in enumerate(test_logs, 1):
        print(f"\n输入日志 {i}:")
        print(f"  {log[:80]}...")

        result = parser.parse(log)

        if result:
            print(f"✓ 解析成功")
            print(f"  使用模式: {result.get('parser', 'unknown')}")
            print(f"  解析字段: {list(result.keys())}")
            results.append({
                'input': log,
                'output': result,
                'status': 'success'
            })
        else:
            print(f"✗ 解析失败")
            results.append({
                'input': log,
                'output': None,
                'status': 'failed'
            })

    # 写入文件
    output_file.write("\n" + "="*60 + "\n")
    output_file.write("测试 3: Logparser 解析器（自动检测模式）\n")
    output_file.write("="*60 + "\n\n")

    for i, result in enumerate(results, 1):
        output_file.write(f"输入日志 {i}:\n")
        output_file.write(f"  {result['input']}\n\n")
        output_file.write(f"解析状态: {result['status']}\n")
        if result['output']:
            output_file.write(f"使用模式: {result['output'].get('parser', 'unknown')}\n")
            output_file.write(f"解析结果:\n")
            output_file.write(json.dumps(result['output'], ensure_ascii=False, indent=2, default=str))
            output_file.write("\n")
        output_file.write("\n" + "-"*60 + "\n\n")

    print(f"\n✓ Logparser 解析器测试完成，共 {len(results)} 条")


def test_data_cleaner(output_file):
    """测试数据清洗器并输出结果"""
    print("\n" + "="*60)
    print("测试 4: 数据清洗器")
    print("="*60)

    cleaner = DataCleaner(config={
        'filters': [
            {'field': 'username', 'operator': 'not_equals', 'value': 'test'},
        ],
        'field_mappings': {
            'username': 'user',
            'source_ip': 'ip',
        },
        'cleaning_rules': [
            {'type': 'default_value', 'field': 'severity_level', 'default': 'INFO'},
            {'type': 'transform', 'field': 'username', 'function': 'lowercase'},
        ],
    })

    test_records = [
        {
            'user': '  Admin  ',
            'ip': '192.168.1.1',
            'action': 'LOGIN',
        },
        {
            'user': 'test',
            'ip': '10.0.0.1',
            'action': 'API_CALL',
        },
        {
            'user': 'ZhangSan',
            'ip': '172.16.0.10',
            'action': 'LOGOUT',
            'empty_field': '',
        },
    ]

    results = []
    for i, record in enumerate(test_records, 1):
        print(f"\n输入记录 {i}:")
        print(f"  {record}")

        result = cleaner.clean(record)

        if result:
            print(f"✓ 清洗成功")
            print(f"  清洗后字段: {list(result.keys())}")
            results.append({
                'input': record,
                'output': result,
                'status': 'cleaned'
            })
        else:
            print(f"✗ 被过滤")
            results.append({
                'input': record,
                'output': None,
                'status': 'filtered'
            })

    # 写入文件
    output_file.write("\n" + "="*60 + "\n")
    output_file.write("测试 4: 数据清洗器\n")
    output_file.write("="*60 + "\n\n")

    for i, result in enumerate(results, 1):
        output_file.write(f"输入记录 {i}:\n")
        output_file.write(f"  {json.dumps(result['input'], ensure_ascii=False, indent=2)}\n\n")
        output_file.write(f"处理状态: {result['status']}\n")
        if result['output']:
            output_file.write(f"清洗结果:\n")
            output_file.write(json.dumps(result['output'], ensure_ascii=False, indent=2, default=str))
            output_file.write("\n")
        output_file.write("\n" + "-"*60 + "\n\n")

    print(f"\n✓ 数据清洗器测试完成，共 {len(results)} 条")


def test_log_processor(output_file):
    """测试 LogProcessor 完整流程并输出结果"""
    print("\n" + "="*60)
    print("测试 5: LogProcessor 完整流程")
    print("="*60)

    processor = LogProcessor(config={})

    test_logs = [
        '{"timestamp": "2024-01-01T10:00:00Z", "username": "admin", "action": "login", "source_ip": "192.168.1.1"}',
        "2024-01-01 10:05:00 LOGIN user=zhangsan ip=10.0.0.5 status=SUCCESS",
        '192.168.1.100 - - [01/Jan/2024:12:00:00 +0800] "GET /api/users HTTP/1.1" 200 1234 "-" "Mozilla/5.0"',
        "2024-01-01T12:05:00Z POST /api/v1/login user=lisi status=401 response_time=120.3ms",
    ]

    results = []
    for i, log in enumerate(test_logs, 1):
        print(f"\n处理日志 {i}:")
        print(f"  输入: {log[:80]}...")

        # 解析
        parsed = processor.parse_log(log)
        if parsed:
            print(f"  ✓ 解析成功")

            # 清洗
            cleaned = processor.clean_log(parsed)
            if cleaned:
                print(f"  ✓ 清洗成功")
                results.append({
                    'input': log,
                    'parsed': parsed,
                    'cleaned': cleaned,
                    'status': 'success'
                })
            else:
                print(f"  ✗ 清洗失败")
                results.append({
                    'input': log,
                    'parsed': parsed,
                    'cleaned': None,
                    'status': 'clean_failed'
                })
        else:
            print(f"  ✗ 解析失败")
            results.append({
                'input': log,
                'parsed': None,
                'cleaned': None,
                'status': 'parse_failed'
            })

    # 写入文件
    output_file.write("\n" + "="*60 + "\n")
    output_file.write("测试 5: LogProcessor 完整流程（解析 + 清洗）\n")
    output_file.write("="*60 + "\n\n")

    for i, result in enumerate(results, 1):
        output_file.write(f"日志 {i}:\n")
        output_file.write(f"  输入: {result['input']}\n\n")
        output_file.write(f"  处理状态: {result['status']}\n\n")

        if result['parsed']:
            output_file.write(f"  解析结果:\n")
            output_file.write(json.dumps(result['parsed'], ensure_ascii=False, indent=2, default=str))
            output_file.write("\n\n")

        if result['cleaned']:
            output_file.write(f"  清洗结果:\n")
            output_file.write(json.dumps(result['cleaned'], ensure_ascii=False, indent=2, default=str))
            output_file.write("\n")

        output_file.write("\n" + "="*60 + "\n\n")

    success_count = sum(1 for r in results if r['status'] == 'success')
    print(f"\n✓ LogProcessor 测试完成，成功 {success_count}/{len(results)} 条")


def main():
    """运行所有测试并输出到文件"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " "*15 + "Parsers 模块功能演示测试" + " "*15 + "║")
    print("╚" + "="*58 + "╝")

    # 创建输出文件
    output_dir = Path(__file__).parent.parent.parent / 'tests' / 'parser' / 'output'
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file_path = output_dir / f'test_results_{timestamp}.txt'

    print(f"\n📝 测试结果将保存到: {output_file_path}")
    print("="*60)

    with open(output_file_path, 'w', encoding='utf-8') as output_file:
        # 写入测试头信息
        output_file.write("Parsers 模块功能演示测试报告\n")
        output_file.write("="*60 + "\n")
        output_file.write(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        output_file.write("="*60 + "\n")

        # 运行测试
        test_json_parser(output_file)
        test_regex_parser(output_file)
        test_logparser(output_file)
        test_data_cleaner(output_file)
        test_log_processor(output_file)

        # 写入总结
        output_file.write("\n" + "="*60 + "\n")
        output_file.write("测试总结\n")
        output_file.write("="*60 + "\n\n")
        output_file.write("✓ 所有测试完成！\n")
        output_file.write(f"✓ 详细结果已保存到: {output_file_path}\n")

    print("\n" + "="*60)
    print("✓ 所有测试完成！")
    print(f"✓ 详细结果已保存到: {output_file_path}")
    print("="*60)


if __name__ == '__main__':
    main()
