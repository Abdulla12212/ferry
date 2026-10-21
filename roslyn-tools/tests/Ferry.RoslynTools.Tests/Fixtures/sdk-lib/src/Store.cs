using System;
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;

namespace SdkLib
{
    // System.Web in a comment must not count, and neither must "System.Web" in a string.
    public class Store
    {
        private const string Note = "System.Web";

        public void Save(Stream stream, object value)
        {
            var formatter = new BinaryFormatter();
            formatter.Serialize(stream, value);
        }
    }
}
