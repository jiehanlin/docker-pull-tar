为了减小打包的体积，需要在虚拟化环境中打包，以下为打包的步骤。

第一步

``` bat

@REM 可选：虚拟环境统一存放目录，按需改成自己的路径（默认在用户目录下）
set WORKON_HOME=%USERPROFILE%\.virtualenvs

@REM 建立虚拟环境
pipenv install
@REM 进入虚拟环境
pipenv shell

```

第二步，虚拟环境需要单独执行，否则会报错

``` bat
@REM 安装依赖，在虚拟环境中
pip install pyinstaller requests urllib3 tqdm -i https://pypi.tuna.tsinghua.edu.cn/simple/
@REM 打包
pyinstaller -F -n DockerPull.exe -i favicon.ico --version-file=version.txt docker_image_puller.py
@REM 卸载依赖
pipenv uninstall --all
@REM 删除虚拟环境
pipenv --rm 
@REM 退出虚拟环境
exit

```
