using System;
using System.Runtime.InteropServices;

namespace EasySmooth
{
    public class Smooth
    {
        [DllImport("kernel32.dll")]
        public static extern IntPtr LoadLibrary(string dllName);

        [DllImport("kernel32.dll")]
        public static extern IntPtr GetProcAddress(IntPtr hModule, string procName);

        [DllImport("kernel32")]
        public static extern bool FreeLibrary(IntPtr hModule);

        /**
        * 注意所有的函数都需要此函数返回 true 后才可以调用
        * 静态/动态连接驱动,当驱动已经安装到系统时候,
        * 则为静态连接,静态连接驱动不需要调用 SmoothDisconnectDriver 释放驱动.
        * 当驱动未安装到系统时,则为动态调用,动态调用时,在需要以管理员权限调用此接口.
        * 同时在程序退出时,需要调用 SmoothDisconnectDriver
        * 建议 先把驱动安装进系统,然后 使用 Smooth
        * @return bool
        */
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate bool SmoothConnectDriver();
        
        
        /**
        * 从配置文件内自动初始化,配置文件应当存放在程序同目录下,且文件名必须为Config.ini
        * @return bool
        */
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate bool SmoothAutoInitConfigFromFile();
        
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate bool SmoothSetPortOrPhyValue([MarshalAs(UnmanagedType.LPWStr)] string lpAppName, bool pdwPinVal);
        /**
        * 读取 指定名称的输入状态,
        * 此函数只能在 通过  SmoothAutoInitConfigFromFile 初始化成功后使用.
        * @param lpAppName 节名称,其名称可以打开程序目录的Config.ini 查看 一般为 GPIx
        * @param pdwPinVal true/false , 当函数返回 true 时,此值才有实际意义.
        * @return bool
        */
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate int SmoothGetPortOrPhyValue([MarshalAs(UnmanagedType.LPWStr)] string lpAppName);
        
        /**
        * 简易方法,通过地址和位直接读取对应GPIO数据
        * @param {unsigned short} nAddress 地址
        * @param {unsigned char} nOffset 位
        * @return {int} 0 为低电平,1 为高电平,其他为错误
        */
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate int SmoothEasyGetPort(ushort nAddress,byte nOffset);

        /**
        * 简易方法,通过地址和位直接设置对应GPIO数据
        * @param {unsigned short} nAddress 地址
        * @param {unsigned char} nOffset 位
        * @param {unsigned char} value 值, 只能为 0 或者 1
        * @return {bool}
        */
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate bool SmoothEasySetPort(ushort nAddress,byte nOffset,byte nValue);
        
    }
}