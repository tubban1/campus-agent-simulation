#!/usr/bin/env python3
"""
Campus Simulation Frontend & API Automated Smoke Test Script
Verifies:
1. Static asset endpoints (/css/app.css, /css/map.css, /js/app.js, and spatial modules)
2. ES module export integrity for the 2D-first map and profile-only 3D renderer
3. DOM element ID alignment between index.html and app.js bindings
4. FastAPI spatial API responses (/api/spatial/worlds, /api/spatial/scene)
"""
import os
import sys
import time
import socket
import tempfile
import urllib.request
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def check_file_exports():
    print("[1/4] 正在检验前端 ES 模块导出与变量绑定...")
    three_scene_path = PROJECT_ROOT / "frontend" / "js" / "spatial" / "three-scene.js"
    three_content = three_scene_path.read_text(encoding="utf-8")

    for fn in ["export function zoomIn", "export function zoomOut", "export function getObserverZoom", "export function renderThreeLoop", "export function updateSceneLOD", "export function renderProfileCharacter"]:
        assert fn in three_content, f"缺失必要的导出方法: {fn}"
    assert "CORE_TSINGHUA_LANDMARKS" in three_content, "three-scene.js 缺失核心地标列表"
    assert "geometry.rotateX(-Math.PI / 2)" in three_content, "真实建筑 footprint 未向上挤出，可能被地面遮挡"
    assert "toggleCameraMode" in three_content and "bindScenePointerControls" in three_content, "three-scene.js 缺失手动镜头切换或拖拽控制"
    assert "event.shiftKey" in three_content, "three-scene.js 缺失 Shift 拖拽旋转"
    assert "makeAgentPresence" in three_content and "avatarFileFor" in three_content, "three-scene.js 缺失统一的 Agent 头像标记"
    assert "nearActiveCount < 24" in three_content, "three-scene.js 缺失近景 <= 24 标签额度控制"
    assert "userData.posX" in three_content, "three-scene.js 缺失节点坐标 (userData.posX) 视距计算"
    assert 'scenePhase === "loading"' in three_content, "three-scene.js 缺失 loading 阶段无示范校园回退保护"
    print("  ✓ three-scene.js 导出、节点坐标 LOD 与 Loading 防回退校验通过")

    app_js_path = PROJECT_ROOT / "frontend" / "js" / "app.js"
    app_content = app_js_path.read_text(encoding="utf-8")
    assert "initOrUpdateMapLibreMap" in app_content, "app.js 未初始化 MapLibre 2D 地图"
    assert "initThreeScene(null, profileCanvas)" in app_content, "app.js 未将 Three.js 限定为人物档案"
    assert "toggleMapDisplayMode" not in app_content, "app.js 仍暴露 2D/3D 世界地图切换"
    assert "AbortController" in app_content, "app.js 缺失 AbortController 并发中断控制"
    assert "WorldStore.selectWorld" in app_content, "app.js 缺失 WorldStore.selectWorld 选界入口调用"
    assert "renderProfileCharacter(a)" in app_content, "app.js 打开档案时没有恢复 3D 人物"
    assert "renderProfileBase" in app_content and "loadProfileDetails" in app_content, "app.js 缺失居民档案的即时渲染或详情加载"
    assert "openLifeCourse" in app_content and "lifeCourseOverlay" in app_content, "app.js 缺失生命历程弹窗绑定"
    assert "/life-course/overview" in app_content or "/life-course/overview" in (PROJECT_ROOT / "frontend" / "js" / "api-client.js").read_text(encoding="utf-8"), "生命历程未指向现有 overview 接口"
    print("  ✓ app.js 竞态请求令牌与 WorldStore.selectWorld 选界入口校验通过")

    api_client_content = (PROJECT_ROOT / "frontend" / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "/api/simulate" not in api_client_content and "/api/tick" not in api_client_content, "api-client.js 仍保留已移除的模拟写入入口"

    store_path = PROJECT_ROOT / "frontend" / "js" / "spatial" / "world-store.js"
    store_content = store_path.read_text(encoding="utf-8")
    assert "selectWorld" in store_content, "world-store.js 缺失 selectWorld 选界同步入口"
    assert "scenePhase" in store_content, "world-store.js 缺失 scenePhase 状态字段"
    print("  ✓ world-store.js selectWorld 选界入口与 scenePhase 校验通过")

    maplibre_path = PROJECT_ROOT / "frontend" / "js" / "spatial" / "maplibre-map.js"
    maplibre_content = maplibre_path.read_text(encoding="utf-8")
    assert "campus-agents-layer" in maplibre_content and "focusAgentOnMap" in maplibre_content, "maplibre-map.js 缺少 Agent 点击与聚焦处理器"
    assert "triggerMapEventAt" in maplibre_content, "maplibre-map.js 缺少空白地图物理事件触发"
    print("  ✓ maplibre-map.js 交互监听校验通过 (Agent 点击与空白地图物理事件)")

def check_html_ids():
    print("[2/4] 正在检验 index.html 节点 ID 与 JS 绑定一致性...")
    index_html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    required_ids = ["maplibreContainer", "cameraZoomIn", "cameraZoomOut", "worldKeySelector", "observerCardTitle"]
    for req_id in required_ids:
        assert f'id="{req_id}"' in index_html, f"index.html 缺失关键 ID: {req_id}"
    print("  ✓ index.html 节点 ID 完整性校验通过")

def check_http_routes(base_url):
    print(f"[3/4] 正在检验 HTTP 路由与模块可达性 ({base_url})...")
    routes = [
        "/",
        "/css/app.css",
        "/css/map.css",
        "/js/app.js",
        "/js/api-client.js",
        "/js/spatial/world-store.js",
        "/js/spatial/maplibre-map.js",
        "/js/spatial/three-scene.js",
        "/vendor/maplibre/maplibre-gl.js",
        "/vendor/maplibre/maplibre-gl.css",
        "/api/spatial/worlds",
        "/api/spatial/scene?world_key=tsinghua_main"
    ]
    for route in routes:
        url = f"{base_url}{route}"
        req = urllib.request.Request(url, headers={"User-Agent": "SmokeTest/1.0"})
        print(f"  Testing {route} ...")
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            assert status == 200, f"路由 {route} 返回状态码 {status}"
    print("  ✓ 所有静态模块与 spatial 接口 HTTP 200 校验通过")

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def run_smoke_tests():
    print("=" * 60)
    print(" 校园 Agent 模块化前端 & Spatial Runtime 自动化 Smoke Test")
    print("=" * 60)

    check_file_exports()
    check_html_ids()

    temp_dir = tempfile.TemporaryDirectory()
    server_proc = None
    target_port = get_free_port()
    base_url = f"http://127.0.0.1:{target_port}"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    is_running = sock.connect_ex(('127.0.0.1', target_port)) == 0
    sock.close()

    if is_running:
        raise RuntimeError(f"测试端口 {target_port} 已被占用，拒绝复用未知非隔离服务！")

    try:
        tmp_db_path = Path(temp_dir.name) / "smoke_test.db"
        print(f"[+] 在动态隔离端口 {target_port} 启动测试服务，数据库绑定至临时路径: {tmp_db_path}")

        test_env = dict(os.environ)
        test_env["PYTHONPATH"] = str(PROJECT_ROOT)
        test_env["DATABASE_URL"] = ""
        test_env["DB_PATH"] = str(tmp_db_path)
        test_env["WORLD_RUNNER_ENABLED"] = "false"
        test_env["WORLD_RUNTIME_AUTO_START"] = "false"
        test_env["DISABLE_WORLD_RUNNER"] = "true"
        test_env["DISABLE_BACKGROUND_RUNNER"] = "true"
        test_env["SIMULATION_AUTO_START"] = "false"
        test_env["EXTERNAL_SYNC_ENABLED"] = "false"

        # The application no longer creates or repairs schemas during HTTP
        # startup.  Exercise the same explicit fresh-world deployment path
        # that local and Supabase environments use.
        deploy = subprocess.run(
            [sys.executable, "scripts/deploy_database.py"],
            cwd=str(PROJECT_ROOT),
            env=test_env,
            capture_output=True,
            text=True,
        )
        if deploy.returncode:
            raise RuntimeError(
                "隔离测试数据库 bootstrap 失败："
                + (deploy.stdout + deploy.stderr)[-2000:]
            )

        server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(target_port)],
            cwd=str(PROJECT_ROOT),
            env=test_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    if s.connect_ex(('127.0.0.1', target_port)) == 0:
                        time.sleep(1.2)
                        break
            except Exception:
                pass
            time.sleep(0.3)

        if server_proc.poll() is not None:
            output = server_proc.stdout.read() if server_proc.stdout else ""
            raise RuntimeError(f"隔离测试服务启动失败（退出码 {server_proc.returncode}）：{output[-2000:]}")

        check_http_routes(base_url)
        print("[4/4] 正在检验后端逻辑与 Diff 规范...")
        diff_res = subprocess.run(["git", "diff", "--check"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        assert diff_res.returncode == 0, f"git diff --check 发现格式异常:\n{diff_res.stderr}"
        print("  ✓ git diff --check 通过")

    finally:
        if server_proc:
            print("[+] 正在停止隔离测试服务并清理临时 SQLite 数据库...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # A test helper must never strand a Uvicorn process (or make
                # CI wait forever) if an application's shutdown hook stalls.
                server_proc.kill()
                server_proc.wait(timeout=4)
        temp_dir.cleanup()

    print("=" * 60)
    print(" 🎉 ALL FRONTEND & SPATIAL SMOKE TESTS PASSED SAFELY!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        run_smoke_tests()
    except Exception as e:
        print(f"\n❌ SMOKE TEST FAILED: {e}")
        sys.exit(1)
