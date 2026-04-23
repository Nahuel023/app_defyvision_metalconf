using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace fusion.core.Spread
{
    public static class Bits
    {
        /// <summary>
        /// 设置指定位为高位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static byte SetBit(this byte variable, byte bit)
        {
            return (byte)(variable | (1 << bit));

        }
        /// <summary>
        /// 设置指定位为低位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static byte ClrBit(this byte variable, byte bit)
        {
            return (byte)(variable & (~(1 << bit)));
        }
        /// <summary>
        /// 读取指定位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static bool GetBit(this byte variable, byte bit)
        {
            return (variable & (1 << bit)) != 0;
        }



        /// <summary>
        /// 设置指定位为高位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static ushort SetBit(this ushort variable, byte bit)
        {
            return (ushort)(variable | (1 << bit));

        }
        /// <summary>
        /// 设置指定位为低位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static ushort ClrBit(this ushort variable, byte bit)
        {
            return (ushort)(variable & (~(1 << bit)));
        }
        /// <summary>
        /// 读取指定位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static bool GetBit(this ushort variable, byte bit)
        {
            return (variable & (1 << bit)) != 0;
        }

        /// <summary>
        /// 设置指定位为高位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static uint SetBit(this uint variable, byte bit)
        {
            return (uint)(variable | (uint)(1 << bit));

        }
        /// <summary>
        /// 设置指定位为低位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static uint ClrBit(this uint variable, byte bit)
        {
            return (uint)(variable & (~(1 << bit)));
        }
        /// <summary>
        /// 读取指定位
        /// </summary>
        /// <param name="variable"></param>
        /// <param name="bit"></param>
        /// <returns></returns>
        public static bool GetBit(this uint variable, byte bit)
        {
            return (variable & (1 << bit)) != 0;
        }
    }
}
