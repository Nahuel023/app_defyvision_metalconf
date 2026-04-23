using System;
using System.IO;

namespace fusion.Core.Assets
{
    public class FileRelated
    {
        /// <summary>
        /// 复制文件夹,包含子目录
        /// </summary>
        /// <param name="argSrcFolder"></param>
        /// <param name="argDestFolder"></param>
        /// <returns></returns>
        public static bool CopyFolder(string argSrcFolder, string argDestFolder)
        {
            //目标文件夹不存在,则创建目标文件夹
            if (!Directory.Exists(argDestFolder))
            {
                Directory.CreateDirectory(argDestFolder);
            }

            try
            {
                var fileList = Directory.GetFiles(argSrcFolder, "*");
                foreach (var item in fileList)
                {
                    var tempPath = item.Substring(argSrcFolder.Length + 1);
                    File.Copy(Path.Combine(argSrcFolder, tempPath), Path.Combine(argDestFolder, tempPath), true);
                }

                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
        /// <summary>
        /// 写入文件
        /// </summary>
        /// <param name="filePath"></param>
        /// <param name="fileContent"></param>
        /// <returns></returns>
        public static bool WriteFile(string filePath, byte[] fileContent)
        {
            try
            {
                FileStream fsObj = new FileStream(filePath, FileMode.Create);
                fsObj.Write(fileContent, 0, fileContent.Length);
                fsObj.Close();
                return true;
            }
            catch (Exception)
            {
                return false;
            }

        }
    }
}