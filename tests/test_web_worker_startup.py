import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('web_worker_startup',Path(__file__).resolve().parents[1]/'start_web_with_worker.py')
startup=importlib.util.module_from_spec(spec);spec.loader.exec_module(startup)


class Child:
    def __init__(self,code=None):self.code=code;self.terminated=False;self.waited=False
    def poll(self):return self.code
    def terminate(self):self.terminated=True;self.code=0
    def wait(self,timeout):self.waited=True;return self.code


@pytest.mark.parametrize('exit_code',[0,4])
def test_worker_failure_stops_web_and_requests_service_restart(exit_code):
    web=Child();worker=Child(exit_code);children=iter([web,worker])
    assert startup.supervise([['web'],['worker']],popen=lambda _:next(children))==(exit_code or 1)
    assert web.terminated and web.waited and worker.waited


def test_startup_failure_cleans_up_the_started_web():
    web=Child()
    def launch(command):
        if command==['worker']:raise OSError('Worker failed to launch')
        return web
    with pytest.raises(OSError):startup.supervise([['web'],['worker']],popen=launch)
    assert web.terminated and web.waited
