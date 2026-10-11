"""Check prerequisites for the hardware integration example.

Uses only the standard library. Compiles a disposable toolchain probe; never
loads product code, starts equipment, installs packages or contacts the network.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid


def run(command):
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        return {"command": list(map(str, command)), "returncode": process.returncode,
                "output": process.stdout + process.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": list(map(str, command)), "returncode": None, "error": str(error)}


def inspect(form, openssl):
    rows = []
    root_package = Path(__file__).resolve().parent
    bundle_path = root_package / 'bundle.json'
    bundle = json.loads(bundle_path.read_text()) if bundle_path.exists() else {}
    delivery = bundle.get('delivery', 'source')
    if delivery not in {'source', 'supplied-c-runtime'}:
        raise ValueError('Unrecognized package build profile. Extract a fresh supplied archive.')
    supplied = delivery == 'supplied-c-runtime'
    if form is None:
        form = 'service' if supplied and sys.platform == 'linux' else 'direct'

    def add(name, passed, detail, action=""):
        rows.append({"check": name, "status": "passed" if passed else "needs-attention",
                     "detail": detail, "action": "" if passed else action})

    add("Python", sys.version_info >= (3, 12), platform.python_version(),
        "Run this check with Python 3.12 or newer. The supplied walkthrough is tested with Python 3.12.")
    supported_host = sys.platform in {"linux", "darwin"}
    add("Development host", supported_host, platform.system(),
        "Use the Linux development environment described in the example README. No build-host path is validated here for this OS.")
    add("Delivery form", form == "direct" or sys.platform == "linux", form,
        "Use the direct example on macOS, or run the service example in the documented Linux environment.")
    if supplied:
        expected = bundle['runtime_host']
        add('Prebuilt library platform', expected == {'system': platform.system(), 'machine': platform.machine()},
            str(expected), 'Use the archive built for this OS and CPU. Build from source for a different firmware ABI.')
        verified = True
        for name, sha in json.loads((root_package / 'sdk/sha256.json').read_text()).items():
            path = (root_package / name).resolve()
            if (not path.is_relative_to(root_package) or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != sha):
                verified = False
                break
        add('Supplied SDK files', verified, 'Verified' if verified else 'A supplied file is missing or changed',
            'Extract a fresh archive. Keep product changes in product.json and sample/example.c.')
    tools = {}
    remedies = {"cmake": "Install CMake 3.16 or newer.",
                "cc": "Install a C compiler with C11 support.", "c++": "Install a C++ compiler with C++17 support.",
                "openssl": "Install the OpenSSL command-line tool used by the example."}
    if supplied:
        del remedies['c++']
    for name in remedies:
        executable = shutil.which(name)
        if executable:
            row = run([executable, "version" if name == "openssl" else "--version"])
            tools[name] = {"executable": executable, **row}
            lines = row.get("output", row.get("error", "")).splitlines()
            add(name, row["returncode"] == 0, lines[0] if lines else "No version output", remedies[name])
        else:
            tools[name] = {"executable": None}
            add(name, False, "Not found on PATH", remedies[name])
    prefix_ok = openssl is None or openssl.is_dir()
    if openssl:
        add("OpenSSL development directory", prefix_ok, str(openssl),
            "Pass the directory containing your OpenSSL development installation, or omit --openssl for the default search.")
    probe = {"status": "not-run", "reason": "Resolve the prerequisite findings before the compile check."}
    if supported_host and prefix_ok and all(row["status"] == "passed" for row in rows):
        with tempfile.TemporaryDirectory(prefix="openavc-environment-") as temporary:
            root = Path(temporary)
            cmake = (
                "cmake_minimum_required(VERSION 3.16)\nproject(environment_check LANGUAGES " + ('C' if supplied else 'C CXX') + ")\n"
                "find_package(Python3 3.12 REQUIRED COMPONENTS Interpreter)\n"
                "find_package(Threads REQUIRED)\nfind_package(OpenSSL 3 REQUIRED)\n"
                "add_executable(environment-check check.c " + ('main.c' if supplied else 'check.cpp') + ")\n"
                "target_compile_features(environment-check PRIVATE c_std_11" + ('' if supplied else ' cxx_std_17') + ")\n"
                "target_link_libraries(environment-check PRIVATE Threads::Threads OpenSSL::SSL OpenSSL::Crypto)\n")
            if supplied:
                cmake += (
                    'target_include_directories(environment-check PRIVATE "${SDK}/include")\n'
                    'target_link_libraries(environment-check PRIVATE "${SDK}/lib/libruntime.a" "${SDK}/lib/libhttp.a"\n'
                    '  OpenSSL::SSL OpenSSL::Crypto Threads::Threads ${CMAKE_DL_LIBS})\n'
                    'if(CMAKE_SYSTEM_NAME STREQUAL "Linux")\n'
                    '  target_link_libraries(environment-check PRIVATE "${SDK}/lib/libc++.so.1" "${SDK}/lib/libc++abi.so.1" "${SDK}/lib/libunwind.so.1" m)\n'
                    'else()\n  target_link_libraries(environment-check PRIVATE c++)\nendif()\n')
                (root / 'main.c').write_text(
                    '#include <pthread.h>\n#include "endpoint.h"\nint check_c(void);\n'
                    'static void *work(void *value) { return value; }\n'
                    'int main(void) { pthread_t thread; if (pthread_create(&thread, 0, work, 0)) return 1;\n'
                    'pthread_join(thread, 0); return check_c() + (int)oavc_port(0); }\n')
            (root / 'CMakeLists.txt').write_text(cmake)
            (root / "check.c").write_text(
                "#include <stdatomic.h>\n#include <openssl/ssl.h>\n"
                "int check_c(void) { atomic_int value = 0; atomic_fetch_add(&value, 1);\n"
                "SSL_CTX *ctx = SSL_CTX_new(TLS_method()); if (!ctx) return 1; SSL_CTX_free(ctx); return 0; }\n")
            (root / "check.cpp").write_text(
                "#include <optional>\n#include <thread>\nextern \"C\" int check_c(void);\n"
                "int main() { std::optional<int> result; std::thread work([&] { result = check_c(); });\n"
                "work.join(); return result.value_or(1); }\n")
            configure = run([tools["cmake"]["executable"], "-S", str(root), "-B", str(root / "build"),
                "-DPython3_EXECUTABLE=" + sys.executable,
                *(['-DSDK=' + str(root_package / 'sdk')] if supplied else []),
                *(["-DOPENSSL_ROOT_DIR=" + str(openssl)] if openssl else [])])
            probe = {"status": "failed", "configure": configure}
            if configure["returncode"] == 0:
                built = run([tools["cmake"]["executable"], "--build", str(root / "build"), "--parallel", "1"])
                probe["build"] = built
                if built["returncode"] == 0:
                    probe["status"] = "passed"
            add("C11, supplied SDK, threads and OpenSSL 3 development files" if supplied else "C11/C++17, threads and OpenSSL 3 development files",
                probe["status"] == "passed", probe["status"],
                ("Read the configure/build output in this report. Use the matching prebuilt libraries and C compiler; install OpenSSL 3 development files."
                 if supplied else "Read the configure/build output in this report. On Ubuntu install build-essential and libssl-dev; on Homebrew pass --openssl /opt/homebrew/opt/openssl@3."))
    passed = all(row["status"] == "passed" for row in rows)
    return {"format": 1, "status": "prerequisites-verified" if passed else "needs-attention",
            "doctor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "host": {"os": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
            "delivery": delivery, "form": form, "checks": rows, "tools": tools, "compile_probe": probe,
            "scope": "Development-host prerequisites only. No product code or compiled probe was executed. Target firmware, cross-toolchains, hardware behavior and resource capacity are not qualified.",
            "privacy": "Contains local tool paths and compiler output. Nothing is uploaded."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--form", choices=["direct", "service"])
    parser.add_argument("--openssl", type=Path)
    parser.add_argument("--report", type=Path, help="New JSON report path; defaults to a unique file in the current directory")
    args = parser.parse_args()
    if args.report is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.report = Path("openavc-environment-" + stamp + "-" + uuid.uuid4().hex[:6] + ".json")
    # Reserve the destination before running any checks; never overwrite evidence.
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        destination = args.report.open("x")
    except OSError as error:
        print("Cannot create report: " + str(error) + ". Choose a new writable path with --report.", file=sys.stderr)
        return 2
    with destination:
        try:
            result = inspect(args.form, args.openssl.resolve() if args.openssl else None)
        except Exception as error:
            result = {"format": 1, "status": "error", "checks": [], "error": str(error),
                      "scope": "The environment check did not finish. Prerequisites have not been verified."}
        json.dump(result, destination, indent=2)
        destination.write("\n")
    for row in result["checks"]:
        print("{status}: {check}: {detail}".format(**row))
        if row["action"]:
            print("  " + row["action"])
    print("Report: " + str(args.report))
    print(result["scope"])
    if result["status"] == "error":
        print("Diagnostic error: " + result["error"], file=sys.stderr)
        return 2
    return 0 if result["status"] == "prerequisites-verified" else 1


if __name__ == "__main__":
    sys.exit(main())
