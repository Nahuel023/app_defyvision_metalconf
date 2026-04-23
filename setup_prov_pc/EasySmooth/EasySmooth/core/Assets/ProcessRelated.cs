using System;
using System.Diagnostics;
using System.Linq;

namespace fusion.Core.Assets
{
    public class ProcessRelated
    {
        public static int Normal = 0;
        public static int Hidden = 1;
        public static int Minimized = 2;
        public static int Maximized = 3;

        /// <summary>
        /// 多线程锁
        /// </summary>
        private readonly object _lock = new object();

        /// <summary>
        /// 进程列表
        /// </summary>
        private Process[] _processlist;

        public ProcessRelated()
        {
            _processlist = Process.GetProcesses();
        }

        /// <summary>
        /// 刷新进程列表
        /// </summary>
        public void Refresh()
        {
            lock (_lock)
            {
                //清空原始数组
                Array.Clear(_processlist, 0, _processlist.Length);
                _processlist = Process.GetProcesses();
            }
        }

        /// <summary>
        /// 进程是否存在
        /// </summary>
        /// <param name="argProcessName"></param>
        /// <param name="argFilePath"></param>
        /// <returns></returns>
        public bool Exists(string argProcessName, string argFilePath = null)
        {
            lock (_lock)
            {
                Refresh();
                try
                {
                    var nameMatch = false;
                    var pathMatch = false;

                    foreach (var process in _processlist)
                    {
                        if (!string.Equals(process.ProcessName, argProcessName,
                                StringComparison.CurrentCultureIgnoreCase)) continue;
                        nameMatch = true;
                        if (argFilePath == null) continue;
                        if (process.MainModule == null) continue;
                        if (!process.MainModule.FileName.StartsWith(argFilePath)) continue;
                        pathMatch = true;
                        break;
                    }

                    return nameMatch && pathMatch;
                }
                catch (Exception)
                {
                    return false;
                }
            }
        }

        /// <summary>
        /// 结束制定进程
        /// 当存在多个同名进程时，则一起结束
        /// </summary>
        /// <param name="argProcessName"></param>
        public void Kill(string argProcessName)
        {
            lock (_lock)
            {
                try
                {
                    foreach (var process in _processlist)
                    {
                        if (string.Equals(process.ProcessName, argProcessName,
                                StringComparison.CurrentCultureIgnoreCase))
                        {
                            process.Kill(); //结束进程
                        }
                    }
                }
                catch (Exception)
                {
                    // ignored
                }
            }
        }

        /// <summary>
        /// 启动进程
        /// </summary>
        /// <param name="argFileName">程序名称</param>
        /// <param name="argParameters">启动参数</param>
        /// <param name="argWindowStyle">窗口风格</param>
        /// <param name="argWorkingDirectory"></param>
        /// <param name="argWaitForExit">是否等待运行结束</param>
        /// <param name="argAdminPermissions">是否以管理员启动</param>
        /// <returns></returns>
        public static bool Start(string argFileName, string argParameters = null,
            int argWindowStyle = 0
            , string argWorkingDirectory = null, bool argWaitForExit = false, bool argAdminPermissions = false)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = argFileName,
                };
                //当参数不为空，则添加参数
                if (argParameters != null)
                {
                    psi.Arguments = argParameters;
                }

                //判断是否需要以管理员权限运行
                if (argAdminPermissions)
                {
                    psi.Verb = "runas";
                }

                //窗口风格

                if (argWindowStyle == Hidden)
                {
                    psi.WindowStyle = ProcessWindowStyle.Hidden;
                }
                else if (argWindowStyle == Minimized)
                {
                    psi.WindowStyle = ProcessWindowStyle.Minimized;
                }
                else if (argWindowStyle == Maximized)
                {
                    psi.WindowStyle = ProcessWindowStyle.Maximized;
                }
                else
                {
                    psi.WindowStyle = ProcessWindowStyle.Normal;
                }

                if (argWorkingDirectory != null)
                {
                    psi.WorkingDirectory = argWorkingDirectory;
                }

                //启动进程
                var processHandle = Process.Start(psi);

                if (argWaitForExit)
                {
                    processHandle?.WaitForExit();
                }

                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// 以管理员运行
        /// </summary>
        /// <param name="argFileName"></param>
        /// <returns></returns>
        public static bool RunAsAdministrator(string argFileName)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = argFileName,
                    Verb = "runas"
                };
                Process.Start(psi);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}