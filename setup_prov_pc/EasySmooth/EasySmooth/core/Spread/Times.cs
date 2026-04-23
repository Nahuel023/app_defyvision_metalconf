using System;
using System.Diagnostics.CodeAnalysis;

namespace fusion.Core.Spread
{
    [SuppressMessage("ReSharper", "MemberCanBePrivate.Global")]
    public static class Times
    {
        
        /// <summary>
        /// 秒级时间戳转时间对象
        /// </summary>
        /// <param name="argUnixTimeStamp"></param>
        /// <returns></returns>
        public static DateTime ToDateTimeByMillisecond(this long argUnixTimeStamp)
        {
            var dateTime = new DateTime(1970, 1, 1, 0, 0, 0, 0, DateTimeKind.Utc);
            dateTime = dateTime.AddMilliseconds(argUnixTimeStamp).ToLocalTime();
            return dateTime;
        }

        /// <summary>
        /// 毫秒时间戳转时间对象
        /// </summary>
        /// <param name="argUnixTimeStamp"></param>
        /// <returns></returns>
        public static DateTime ToDateTimeBySecond(this long argUnixTimeStamp)
        {
            var dateTime = new DateTime(1970, 1, 1, 0, 0, 0, 0, DateTimeKind.Utc);
            dateTime = dateTime.AddSeconds(argUnixTimeStamp).ToLocalTime();
            return dateTime;
        }
    }
}