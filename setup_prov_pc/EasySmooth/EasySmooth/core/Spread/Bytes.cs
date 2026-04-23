using System;

namespace fusion.Core.Spread
{
    public static class Bytes
    {
        /// <summary>
        /// byte 数组 uint16  注意 byte 长度必须等于 2
        /// </summary>
        /// <param name="argBytes"></param>
        /// <param name="argOutShort"></param>
        /// <returns>bool</returns>
        public static bool ToShort(this byte[] argBytes, out ushort argOutShort)
        {
            argOutShort = 0;
            try
            {
                if (argBytes.Length > 2)
                {
                    return false;
                }

                if (BitConverter.IsLittleEndian)
                {
                    Array.Reverse(argBytes);
                }

                argOutShort = BitConverter.ToUInt16(argBytes, 0);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// uint16 转 byte 数组
        /// </summary>
        /// <param name="argShort"></param>
        /// <param name="argOutBytes"></param>
        /// <returns></returns>
        public static bool ToByte(this ushort argShort, out byte[] argOutBytes)
        {
            argOutBytes = null;
            try
            {
                argOutBytes = BitConverter.GetBytes(argShort);

                if (BitConverter.IsLittleEndian)
                {
                    Array.Reverse(argOutBytes);
                }

                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /**
         * 获取 byte 数组内 指定位置或长度的数组
         */
        public static bool GetBytes(this byte[] argBytes, out byte[] argOutBytes, int argIndex, int argSize)
        {
            argOutBytes = new byte[argSize];
            try
            {
                if ((argIndex + argSize) > argBytes.Length)
                {
                    return false;
                }

                for (var i = 0; i < argSize; ++i)
                {
                    argOutBytes[i] = argBytes[argIndex + i];
                }

                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// 复制 byte 数组
        /// </summary>
        /// <param name="argSrc">来源数组</param>
        /// <param name="argDest">目标数组</param>
        /// <param name="argDestStart"></param>
        /// <param name="argSrcStart"></param>
        /// <param name="argSize"></param>
        public static void CopyTo(this byte[] argSrc, byte[] argDest, int argDestStart, int argSrcStart, int argSize)
        {
            for (var i = 0; i < argSize; ++i)
            {
                argDest[argDestStart + i] = argSrc[argSrcStart + i];
            }
        }

        /// <summary>
        /// 调试输出
        /// </summary>
        /// <param name="argBytes"></param>
        public static void Dump(this byte[] argBytes)
        {
            Console.WriteLine(BitConverter.ToString(argBytes, 0, argBytes.Length).Replace("-", " ") + "\r\n");
        }

        public static string ToHexString(this byte[] argBytes)
        {
            return BitConverter.ToString(argBytes, 0, argBytes.Length).Replace("-", " ");
        }
    }
}