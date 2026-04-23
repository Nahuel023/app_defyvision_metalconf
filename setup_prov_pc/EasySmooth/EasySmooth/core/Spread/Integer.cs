using System;

namespace fusion.Core.Spread
{
    public static class Integer
    {
        public static short toShort(this string argString)
        {
           return Convert.ToInt16(argString);
        }
        
        public static ushort toUshort(this string argString)
        {
            return Convert.ToUInt16(argString);
        }
        
        public static uint toUint(this string argString)
        {
            return Convert.ToUInt32(argString);
        }
        
        public static int toInt(this string argString)
        {
            return Convert.ToInt32(argString);
        }

        public static long toInt64(this string argString)
        {
            return Convert.ToInt64(argString);
        }
        
        public static ulong toUInt64(this string argString)
        {
            return Convert.ToUInt64(argString);
        }
    }
}