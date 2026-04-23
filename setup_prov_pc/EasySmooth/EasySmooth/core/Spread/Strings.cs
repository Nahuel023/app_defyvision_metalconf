using System.Diagnostics.CodeAnalysis;
using System.Text;

namespace fusion.Core.Spread
{
    [SuppressMessage("ReSharper", "MemberCanBePrivate.Global")]
    public static class Strings
    {
        /// <summary>
        /// 字符串转字节数组
        /// </summary>
        /// <param name="argString"></param>
        /// <param name="argDefaultEncoding"></param>
        /// <returns></returns>
        public static byte[] GetBytes(this string argString, Encoding argDefaultEncoding = null)
        {
            if (argString == null)
            {
                return null;
            }

            //如果 argDefaultEncoding 等于 null 的时候,则 argDefaultEncoding 等于 Encoding.ASCII
            if(argDefaultEncoding == null)
            {
                argDefaultEncoding = Encoding.ASCII;
            }
            var result = argDefaultEncoding.GetBytes(argString);
            return result;
        }

        /// <summary>
        /// 字节数组转字符串
        /// </summary>
        /// <param name="argString"></param>
        /// <param name="argDefaultEncoding"></param>
        /// <returns></returns>
        public static string GetString(this byte[] argString, Encoding argDefaultEncoding = null)
        {
            if (argString == null)
            {
                return null;
            }

            if (argDefaultEncoding == null)
            {
                argDefaultEncoding = Encoding.ASCII;
            }
            var result = argDefaultEncoding.GetString(argString);
            return result;
        }

        /// <summary>
        /// 清理字节数组的空字符及原始换行符,然后给字节数组最后添加一个换行符
        /// </summary>
        /// <param name="argString"></param>
        /// <returns></returns>
        public static byte[] AppendNewLine(this byte[] argString)
        {
            return AppendNewLine(argString.GetString()).GetBytes();
        }

        /// <summary>
        /// 清理原字符串的空字符及换行符,然后给字符串增加一个换行符
        /// </summary>
        /// <param name="argString"></param>
        /// <returns></returns>
        public static string AppendNewLine(this string argString)
        {
            return argString.TrimForce() + "\n";
        }

        /// <summary>
        /// 清理字节素组的空字符及换行符
        /// </summary>
        /// <param name="argBytes"></param>
        /// <returns></returns>
        public static byte[] TrimForce(this byte[] argBytes)
        {
            return TrimForce(argBytes.GetString()).GetBytes();
        }

        /// <summary>
        /// 清理字符串的空字符及换行符
        /// </summary>
        /// <param name="argString"></param>
        /// <returns></returns>
        public static string TrimForce(this string argString)
        {
            return argString.Trim().TrimEnd('\r', '\n', '\t', (char)0x00);
        }

        /// <summary>
        /// 判断字符串是否为空或者为NULL
        /// </summary>
        /// <param name="argString"></param>
        /// <returns></returns>
        public static bool IsEmpty(this string argString)
        {
            return argString == null || argString.Equals("");
        }

        public static bool IsEmpty(this byte[] argBytes)
        {
            return argBytes == null || argBytes.Length == 0;
        }
    }
}