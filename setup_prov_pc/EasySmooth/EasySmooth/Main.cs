using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace EasySmooth
{
    public partial class Main : Form
    {
        IntPtr _hMod;

        private Smooth.SmoothEasyGetPort SmoothEasyGetPort = null;
        private Smooth.SmoothEasySetPort SmoothEasySetPort = null;

        public Main()
        {
            InitializeComponent();
        }

        private void Main_Load(object sender, EventArgs e)
        {
            switch (IntPtr.Size)
            {
                case 4:
                    _hMod = Smooth.LoadLibrary("Smooth32.dll");
                    break;
                case 8:
                    _hMod = Smooth.LoadLibrary("Smooth64.dll");
                    break;
            }

            if (_hMod == IntPtr.Zero)
            {
                MessageBox.Show("没有找到 Smooth64.dll或Smooth32.dll\r\n请确保可执行文件与动态库文件在同一目录!", @"SmoothTools",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                this.Close();
            }

            var pFunc = Smooth.GetProcAddress(_hMod, "SmoothConnectDriver");

            if (pFunc == IntPtr.Zero) return;
            var smoothConnectDriver =
                (Smooth.SmoothConnectDriver)Marshal.GetDelegateForFunctionPointer(pFunc,
                    typeof(Smooth.SmoothConnectDriver));
            if (smoothConnectDriver == null) throw new ArgumentNullException(nameof(smoothConnectDriver));
            var result = smoothConnectDriver();
            if (!result)
            {
                MessageBox.Show(@"驱动连接失败!请检查是否安装驱动,或者以管理员权限运行!", @"SmoothTools", MessageBoxButtons.OK,
                MessageBoxIcon.Error);
                Smooth.FreeLibrary(_hMod);
                this.Close();

            }

            var pSmoothEasyGetPort = Smooth.GetProcAddress(_hMod, "SmoothEasyGetPort");
            if (pSmoothEasyGetPort != IntPtr.Zero)
            {
                SmoothEasyGetPort =
                    (Smooth.SmoothEasyGetPort)Marshal.GetDelegateForFunctionPointer(pSmoothEasyGetPort,
                        typeof(Smooth.SmoothEasyGetPort));
            }

            var pSmoothEasySetPort = Smooth.GetProcAddress(_hMod, "SmoothEasySetPort");
            if (pSmoothEasySetPort != IntPtr.Zero)
            {
                SmoothEasySetPort =
                    (Smooth.SmoothEasySetPort)Marshal.GetDelegateForFunctionPointer(pSmoothEasySetPort,
                        typeof(Smooth.SmoothEasySetPort));
            }

        }

        private void button1_Click(object sender, EventArgs e)
        {
            var PortAddr = UInt16.Parse(txtPortAddr.Text, System.Globalization.NumberStyles.HexNumber);
            var PortOffset = UInt32.Parse(txtValue.Text, System.Globalization.NumberStyles.HexNumber);

            textBox1.Text = SmoothEasyGetPort(PortAddr, (byte)PortOffset) + "";
            

        }

        private void button2_Click(object sender, EventArgs e)
        {
            var PortAddr = UInt16.Parse(txtPortAddr.Text, System.Globalization.NumberStyles.HexNumber);
            var PortOffset = UInt32.Parse(txtValue.Text, System.Globalization.NumberStyles.HexNumber);
            var PortVal = UInt32.Parse(textBox1.Text, System.Globalization.NumberStyles.HexNumber);
            textBox1.Text = SmoothEasySetPort(PortAddr, (byte)PortOffset, (byte)PortVal) + "";
        }
    }
}
