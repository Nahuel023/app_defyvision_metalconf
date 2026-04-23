using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace fusion.Core.Assets
{
    public class Configuration
    {
        [DllImport("kernel32")]
        private static extern long WritePrivateProfileString(string section, string key, string val, string filePath);

        [DllImport("kernel32")]
        private static extern int GetPrivateProfileString(string section, string key, string def, StringBuilder retVal,
            int size, string filePath);


        [DllImport("kernel32")]
        private static extern uint GetPrivateProfileSectionNames(IntPtr pszReturnBuffer, uint nSize, string lpFileName);


        private readonly string _configFileName = null;


        public bool Encrypt { get; set; }

        public Configuration(string argFileName)
        {
            Encrypt = false;
            _configFileName = Path.GetFullPath(argFileName);
        }

        /// <summary>
        /// 读配置项值
        /// </summary>
        /// <param name="argSectionName">节名</param>
        /// <param name="argItemName">项名</param>
        /// <param name="argDefault">默认值</param>
        /// <returns></returns>
        public string GetKeyText(string argSectionName, string argItemName, string argDefault)
        {
            var tempStringBuilder = new StringBuilder(2048);
            GetPrivateProfileString(argSectionName, argItemName, argDefault, tempStringBuilder, tempStringBuilder.Capacity, _configFileName);
            return tempStringBuilder.ToString();
        }

        /// <summary>
        /// 写配置项目
        /// </summary>
        /// <param name="argSectionName">节名</param>
        /// <param name="argItemName">项名</param>
        /// <param name="argString">值</param>
        /// <returns></returns>
        public bool SetKeyText(string argSectionName, string argItemName, string argString)
        {
            var results = WritePrivateProfileString(argSectionName, argItemName, argString, _configFileName);
            return results != 0;
        }

        public List<string> GetSectionNames()
        {
            const uint maxBuffer = 32767;
            var pReturnedString = Marshal.AllocCoTaskMem((int)maxBuffer);
            var bytesReturned = GetPrivateProfileSectionNames(pReturnedString, maxBuffer, _configFileName);
            if (bytesReturned == 0)
            {
                return null;
            }

            var local = Marshal.PtrToStringAnsi(pReturnedString, (int)bytesReturned).ToString();
            Marshal.FreeCoTaskMem(pReturnedString);
            //use of Substring below removes terminating null for split
            return local.Substring(0, local.Length - 1).Split('\0').ToList();
        }
    }
}